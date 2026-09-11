"""凭据加解密（GitHub Token / LLM Key 服务端存储用）。

优先使用 cryptography 的 Fernet（AES-CBC + HMAC）；无法导入时退回标准库实现
（PBKDF2 派生密钥 + HMAC-SHA256 计数器流 + HMAC 校验）。

两种格式用前缀区分，可长期共存：
- f1: Fernet 密文
- v1: 标准库密文

密钥来源：环境变量 CREDENTIAL_KEY；未设置时生成并持久化到 instance/credential.key，
保证服务重启后已存储的凭据仍能解密（该文件请勿删除，也不要提交到仓库）。
"""
import base64
import hashlib
import hmac
import os

KEY_INFO = b"codevoyage-credentials-v1"
_key_cache: bytes | None = None
_fernet = None
_fernet_ready = False


def _instance_dir() -> str:
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
    os.makedirs(base, exist_ok=True)
    return base


def _raw_secret() -> str:
    env = (os.getenv("CREDENTIAL_KEY") or "").strip()
    if env:
        return env
    path = os.path.join(_instance_dir(), "credential.key")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            saved = f.read().strip()
        if saved:
            return saved
    generated = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    with open(path, "w", encoding="utf-8") as f:
        f.write(generated)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return generated


def _key() -> bytes:
    global _key_cache
    if _key_cache is None:
        _key_cache = hashlib.pbkdf2_hmac("sha256", _raw_secret().encode("utf-8"), KEY_INFO, 120_000, dklen=32)
    return _key_cache


def _fernet_obj():
    """返回 Fernet 实例；未安装 cryptography 时返回 None。"""
    global _fernet, _fernet_ready
    if not _fernet_ready:
        _fernet_ready = True
        try:
            from cryptography.fernet import Fernet

            _fernet = Fernet(base64.urlsafe_b64encode(_key()))
        except Exception:
            _fernet = None
    return _fernet


# ------------------------------ 标准库回退实现 ------------------------------
def _keystream(nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    key = _key()
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def _v1_encrypt(data: bytes) -> str:
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _keystream(nonce, len(data))))
    tag = hmac.new(_key(), nonce + ct, hashlib.sha256).digest()
    return "v1:" + base64.urlsafe_b64encode(nonce + tag + ct).decode("ascii")


def _v1_decrypt(token: str) -> bytes:
    raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
    nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
    if not hmac.compare_digest(tag, hmac.new(_key(), nonce + ct, hashlib.sha256).digest()):
        raise ValueError("凭据校验失败：CREDENTIAL_KEY 可能已更换")
    return bytes(a ^ b for a, b in zip(ct, _keystream(nonce, len(ct))))


# ------------------------------ 对外接口 ------------------------------
def encrypt(plaintext: str) -> str:
    """加密字符串，返回带前缀的密文。"""
    data = (plaintext or "").encode("utf-8")
    fernet = _fernet_obj()
    if fernet is not None:
        return "f1:" + fernet.encrypt(data).decode("ascii")
    return _v1_encrypt(data)


def decrypt(token: str) -> str:
    """解密字符串；密文损坏或密钥不符时抛 ValueError。"""
    token = (token or "").strip()
    if not token:
        return ""
    if token.startswith("f1:"):
        fernet = _fernet_obj()
        if fernet is None:
            raise ValueError("该凭据由 cryptography 加密，但当前环境未安装该库")
        return fernet.decrypt(token[3:].encode("ascii")).decode("utf-8")
    if token.startswith("v1:"):
        return _v1_decrypt(token).decode("utf-8")
    raise ValueError("无法识别的凭据格式")


def masked(value: str, head: int = 4, tail: int = 4) -> str:
    """脱敏展示：保留首尾若干位。"""
    value = value or ""
    if not value:
        return ""
    if len(value) <= head + tail:
        return value[:2] + "*" * max(0, len(value) - 2)
    return f"{value[:head]}{'*' * 8}{value[-tail:]}"


def backend() -> str:
    """当前加密后端，便于排查。"""
    return "fernet" if _fernet_obj() is not None else "stdlib"
