"""本地 Agent 数据目录与基础路径管理。

所有本地状态默认保存在 ~/.CodeVoyage/：
    user/conf.json            远端登录凭证 {ID, token, email}
    centre.json               本地 centre 配置 {remote: 远端地址}
    local.json                本地敏感配置（GitHub PAT / LLM Key 等，XOR 混淆后落盘）
    secret.key                混淆用随机密钥（首次启动生成）
    Agent/repo/<repo_hash>/   每个仓库的处理数据目录
    Agent/state.json          全局运行状态（供 Agent 可视化）
    Agent/agent.log           运行日志
"""
import base64
import json
import os
import secrets

HOME_DIR = os.path.expanduser("~")
BASE_DIR = os.path.join(HOME_DIR, ".CodeVoyage")
USER_CONF_PATH = os.path.join(BASE_DIR, "user", "conf.json")
CENTRE_CONF_PATH = os.path.join(BASE_DIR, "centre.json")
LOCAL_CONF_PATH = os.path.join(BASE_DIR, "local.json")
SECRET_KEY_PATH = os.path.join(BASE_DIR, "secret.key")
AGENT_DIR = os.path.join(BASE_DIR, "Agent")
REPO_DIR = os.path.join(AGENT_DIR, "repo")
STATE_PATH = os.path.join(AGENT_DIR, "state.json")
LOG_PATH = os.path.join(AGENT_DIR, "agent.log")

REMOTE_DEFAULT = os.getenv("REMOTE_BASE", "https://CodeVoyage.yjlt.top")


def ensure_dirs() -> None:
    for p in (BASE_DIR, os.path.join(BASE_DIR, "user"), AGENT_DIR, REPO_DIR):
        os.makedirs(p, exist_ok=True)


def _load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default if default is not None else {}


def _save_json(path: str, data) -> None:
    ensure_dirs()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ------------------------------ 远端地址 ------------------------------
def remote_base() -> str:
    conf = _load_json(CENTRE_CONF_PATH)
    return (conf.get("remote") or REMOTE_DEFAULT).rstrip("/")


def set_remote_base(url: str) -> str:
    conf = _load_json(CENTRE_CONF_PATH)
    conf["remote"] = (url or REMOTE_DEFAULT).rstrip("/")
    _save_json(CENTRE_CONF_PATH, conf)
    return conf["remote"]


# ------------------------------ 登录凭证 ------------------------------
def load_user_conf() -> dict | None:
    conf = _load_json(USER_CONF_PATH)
    return conf if conf.get("ID") and conf.get("token") else None


def save_user_conf(uid, token, email) -> dict:
    conf = {"ID": uid, "token": token, "email": email}
    _save_json(USER_CONF_PATH, conf)
    return conf


def clear_user_conf() -> None:
    try:
        os.remove(USER_CONF_PATH)
    except OSError:
        pass


# ------------------------------ 本地敏感配置（混淆落盘） ------------------------------
def _key() -> bytes:
    ensure_dirs()
    if not os.path.exists(SECRET_KEY_PATH):
        key = secrets.token_bytes(32)
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(key)
    with open(SECRET_KEY_PATH, "rb") as f:
        return f.read()


def _encrypt(plain: str) -> str:
    key = _key()
    data = plain.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.b64encode(xored).decode("ascii")


def _decrypt(enc: str) -> str:
    try:
        key = _key()
        xored = base64.b64decode(enc.encode("ascii"))
        data = bytes(b ^ key[i % len(key)] for i, b in enumerate(xored))
        return data.decode("utf-8")
    except Exception:
        return ""


def load_local_conf() -> dict:
    """返回解密后的明文配置（仅进程内使用，绝不外传）。"""
    raw = _load_json(LOCAL_CONF_PATH)
    out = {}
    for k, v in raw.items():
        out[k] = _decrypt(v) if v else ""
    return out


def save_local_conf(values: dict) -> None:
    raw = {k: _encrypt(v) for k, v in values.items() if v is not None}
    _save_json(LOCAL_CONF_PATH, raw)


# ------------------------------ Agent 数据目录 ------------------------------
def repo_hash(repo_full: str) -> str:
    import hashlib

    return hashlib.sha1(repo_full.encode("utf-8")).hexdigest()[:20]


def repo_dir(repo_full: str) -> str:
    return os.path.join(REPO_DIR, repo_hash(repo_full))


def wait_file(repo_full: str) -> str:
    return os.path.join(repo_dir(repo_full), "wait", "list.json")


def history_file(repo_full: str) -> str:
    return os.path.join(repo_dir(repo_full), "history.json")


def info_file(repo_full: str) -> str:
    return os.path.join(repo_dir(repo_full), "Info.json")


def work_dir(repo_full: str) -> str:
    return os.path.join(repo_dir(repo_full), "work")


def load_json(path: str, default=None):
    return _load_json(path, default)


def save_json(path: str, data) -> None:
    _save_json(path, data)


def append_log(message: str) -> None:
    ensure_dirs()
    from datetime import datetime

    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def tail_log(limit: int = 200) -> str:
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(lines[-limit:])
    except OSError:
        return ""
