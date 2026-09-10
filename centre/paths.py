"""本地 Agent 数据目录与基础路径管理。

本地状态默认保存在 ~/.CodeVoyage/（可用环境变量 CODEVOYAGE_HOME 覆盖）：
    user/conf.json            远端登录凭证 {ID, token, email}
    local.json                本地敏感配置（传统令牌 / LLM Key / 提交身份，混淆后落盘）
    repo_tokens.json          各仓库专属的细粒度令牌（混淆后落盘）
    secret.key                混淆用随机密钥（首次启动生成，务必与配置一起保留）
    Agent/repo/<repo_hash>/   每个仓库的处理数据目录
    Agent/state.json          全局运行状态（供 Agent 可视化）
    Agent/agent.log           运行日志

若 ~/.CodeVoyage 不可写（例如受限环境），自动回退到项目内的 .codevoyage-local，
实际路径可通过 /api/local/diagnostics 或控制台「本地配置」页查看。
"""
import base64
import json
import os
import secrets
from datetime import datetime


class StorageError(RuntimeError):
    """本地存储写入失败（路径 / 原因），用于把错误明确暴露给界面。"""

    def __init__(self, path: str, reason):
        super().__init__(f"本地保存失败：{path}（{reason}）")
        self.path = path
        self.reason = str(reason)


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def _data_score(path: str) -> float:
    """该目录里已有本地数据的“新鲜度”时间戳；没有数据返回 0。"""
    markers = (
        os.path.join(path, "user", "conf.json"),
        os.path.join(path, "local.json"),
        os.path.join(path, "repo_tokens.json"),
        os.path.join(path, "secret.key"),
    )
    latest = 0.0
    for marker in markers:
        try:
            latest = max(latest, os.stat(marker).st_mtime)
        except OSError:
            pass
    return latest


def _resolve_base_dir() -> tuple[str, str]:
    """确定本地存储目录。

    优先级：
      1. 环境变量 CODEVOYAGE_HOME（固定目录，推荐长期使用）
      2. 已存在数据且更新的目录（无论是 ~/.CodeVoyage 还是项目内 .codevoyage-local）
         —— 避免因 HOME 变化导致“配置看起来丢了”
      3. ~/.CodeVoyage（可写时）
      4. 项目内 .codevoyage-local（回退）
    """
    env_home = os.getenv("CODEVOYAGE_HOME")
    if env_home:
        base = os.path.abspath(os.path.expanduser(env_home))
        os.makedirs(base, exist_ok=True)
        return base, "env:CODEVOYAGE_HOME"

    home_dir = os.path.join(os.path.expanduser("~"), ".CodeVoyage")
    project_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".codevoyage-local")
    )

    home_score = _data_score(home_dir)
    project_score = _data_score(project_dir)
    if project_score and project_score > home_score:
        return project_dir, "resume:project"
    if home_score:
        return home_dir, "resume:home"
    if _writable(home_dir):
        return home_dir, "home"
    if _writable(project_dir):
        return project_dir, "fallback:project"
    return home_dir, "home"


HOME_DIR = os.path.expanduser("~")
BASE_DIR, BASE_SOURCE = _resolve_base_dir()
USER_CONF_PATH = os.path.join(BASE_DIR, "user", "conf.json")
LOCAL_CONF_PATH = os.path.join(BASE_DIR, "local.json")
REPO_TOKENS_PATH = os.path.join(BASE_DIR, "repo_tokens.json")
SECRET_KEY_PATH = os.path.join(BASE_DIR, "secret.key")
AGENT_DIR = os.path.join(BASE_DIR, "Agent")
REPO_DIR = os.path.join(AGENT_DIR, "repo")
STATE_PATH = os.path.join(AGENT_DIR, "state.json")
LOG_PATH = os.path.join(AGENT_DIR, "agent.log")

# 远端服务地址：写死为现有部署，仅允许用环境变量 CODEVOYAGE_REMOTE 在特殊场景覆盖
REMOTE_DEFAULT = os.getenv("CODEVOYAGE_REMOTE", "https://CodeVoyage.yjlt.top").rstrip("/")


def ensure_dirs() -> None:
    for p in (BASE_DIR, os.path.join(BASE_DIR, "user"), AGENT_DIR, REPO_DIR):
        try:
            os.makedirs(p, exist_ok=True)
        except OSError as e:
            raise StorageError(p, e) from e


def _load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default if default is not None else {}


def _save_json(path: str, data) -> None:
    """原子写入；失败时抛出 StorageError（界面会看到明确路径与原因）。"""
    try:
        ensure_dirs()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except StorageError:
        raise
    except OSError as e:
        raise StorageError(path, e) from e


def storage_info() -> dict:
    """本地存储诊断：实际路径、来源、各文件是否存在/大小/修改时间。"""
    watched = {
        "user/conf.json（登录凭证）": USER_CONF_PATH,
        "local.json（令牌/LLM/提交身份）": LOCAL_CONF_PATH,
        "repo_tokens.json（仓库专属令牌）": REPO_TOKENS_PATH,
        "secret.key（混淆密钥）": SECRET_KEY_PATH,
        "Agent/agent.log（运行日志）": LOG_PATH,
    }
    files = []
    for label, path in watched.items():
        try:
            st = os.stat(path)
            files.append({
                "name": label, "path": path, "exists": True, "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except OSError:
            files.append({"name": label, "path": path, "exists": False, "size": 0, "mtime": ""})
    writable = True
    try:
        ensure_dirs()
        probe = os.path.join(BASE_DIR, ".write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except Exception:
        writable = False
    return {"base_dir": BASE_DIR, "source": BASE_SOURCE, "writable": writable, "files": files}


# ------------------------------ 远端地址（写死） ------------------------------
def remote_base() -> str:
    return REMOTE_DEFAULT


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
    """合并写入本地配置（不删除未提及的键），值统一混淆落盘。"""
    raw = _load_json(LOCAL_CONF_PATH)
    for key, value in (values or {}).items():
        if value is None:
            continue
        raw[key] = _encrypt(value)
    _save_json(LOCAL_CONF_PATH, raw)


def clear_local_conf(keys: list) -> None:
    """删除指定本地配置项（如 github_token / llm_api_key）。"""
    raw = _load_json(LOCAL_CONF_PATH)
    changed = False
    for key in keys:
        if key in raw:
            raw.pop(key)
            changed = True
    if changed:
        _save_json(LOCAL_CONF_PATH, raw)


def mask(value: str, head: int = 4, tail: int = 4) -> str:
    """脱敏展示：保留首尾各若干字符，中间用 * 代替。"""
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= head + tail:
        return "*" * len(value)
    return f"{value[:head]}{'*' * 6}{value[-tail:]}"


# ------------------------------ 仓库专属令牌（细粒度，支持多个并存） ------------------------------
def _normalize_token_list(value) -> list:
    """兼容旧的单值写法，统一成去重后的列表。"""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    out = []
    for item in items:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return out


def _decode_token(enc) -> str:
    """解码已混淆的令牌；若本身就是明文令牌（历史数据）则原样返回。"""
    value = str(enc or "").strip()
    if not value:
        return ""
    token = _decrypt(value)
    if token:
        return token
    if value.startswith(("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")):
        return value
    return ""


def load_repo_tokens_map() -> dict:
    """返回 {repo_full: [明文令牌, ...]}；仅本机使用（兼容旧的单值格式）。"""
    raw = _load_json(REPO_TOKENS_PATH)
    result = {}
    for repo_full, value in raw.items():
        tokens = []
        for enc in _normalize_token_list(value):
            token = _decode_token(enc)
            if token and token not in tokens:
                tokens.append(token)
        if tokens:
            result[repo_full] = tokens
    return result


def load_repo_tokens(repo_full: str) -> list:
    return load_repo_tokens_map().get(repo_full, [])


def add_repo_token(repo_full: str, token: str) -> None:
    """追加一个仓库专属令牌（不覆盖已有，避免重复加密）。"""
    token = (token or "").strip()
    if not token:
        return
    plain = load_repo_tokens(repo_full)  # 已解密的明文列表
    if token not in plain:
        plain.append(token)
    raw = _load_json(REPO_TOKENS_PATH)
    raw[repo_full] = [_encrypt(t) for t in plain]
    _save_json(REPO_TOKENS_PATH, raw)


def remove_repo_token(repo_full: str, index: int | None = None, token: str | None = None) -> None:
    """按序号或按明文令牌删除某个仓库令牌。"""
    plain = load_repo_tokens(repo_full)
    keep = [t for i, t in enumerate(plain)
            if not ((index is not None and i == index) or (token is not None and t == token))]
    raw = _load_json(REPO_TOKENS_PATH)
    if keep:
        raw[repo_full] = [_encrypt(t) for t in keep]
    else:
        raw.pop(repo_full, None)
    _save_json(REPO_TOKENS_PATH, raw)


def clear_repo_token(repo_full: str) -> None:
    """清空某个仓库的全部专属令牌。"""
    raw = _load_json(REPO_TOKENS_PATH)
    if repo_full in raw:
        raw.pop(repo_full)
        _save_json(REPO_TOKENS_PATH, raw)


# ------------------------------ 全局令牌（传统，支持多个并存） ------------------------------
def load_global_tokens() -> list:
    """全局令牌列表；兼容旧的单值 github_token（两者都可能是混淆后的密文）。"""
    conf = _load_json(LOCAL_CONF_PATH)
    raw_list = _normalize_token_list(conf.get("github_tokens"))
    legacy = conf.get("github_token")
    if legacy:
        raw_list.append(str(legacy))
    out = []
    for enc in raw_list:
        token = _decode_token(enc)
        if token and token not in out:
            out.append(token)
    return out


def save_global_tokens(tokens: list) -> None:
    conf = _load_json(LOCAL_CONF_PATH)
    conf["github_tokens"] = [_encrypt(t) for t in _normalize_token_list(tokens)]
    conf.pop("github_token", None)
    _save_json(LOCAL_CONF_PATH, conf)


def add_global_token(token: str) -> list:
    token = (token or "").strip()
    tokens = load_global_tokens()
    if token and token not in tokens:
        tokens.append(token)
    save_global_tokens(tokens)
    return tokens


def remove_global_token(index: int | None = None, token: str | None = None) -> list:
    tokens = load_global_tokens()
    keep = [t for i, t in enumerate(tokens) if not ((index is not None and i == index) or (token is not None and t == token))]
    save_global_tokens(keep)
    return keep


def move_global_token(index: int, delta: int) -> list:
    """调整全局令牌顺序（顺序即回退尝试顺序）。"""
    tokens = load_global_tokens()
    target = index + delta
    if 0 <= index < len(tokens) and 0 <= target < len(tokens):
        tokens[index], tokens[target] = tokens[target], tokens[index]
        save_global_tokens(tokens)
    return tokens


# ------------------------------ 令牌解析（顺序回退） ------------------------------
def resolve_tokens(repo_full: str) -> list:
    """返回该仓库可用的候选令牌，顺序即尝试顺序：仓库专属（细粒度）→ 全局（传统）。

    每项为 (token, source)，source ∈ {'repo', 'global'}。
    """
    candidates = []
    for token in load_repo_tokens(repo_full or ""):
        candidates.append((token, "repo"))
    for token in load_global_tokens():
        if all(token != t for t, _ in candidates):
            candidates.append((token, "global"))
    return candidates


def resolve_token(repo_full: str) -> tuple[str, str]:
    """取第一个候选令牌（向后兼容）。"""
    candidates = resolve_tokens(repo_full)
    return candidates[0] if candidates else ("", "")


def token_hint_list(tokens: list) -> list:
    return [mask(t) for t in tokens]


# ------------------------------ 提交身份 ------------------------------
DEFAULT_GIT_NAME = "CodeVoyage AI"
DEFAULT_GIT_EMAIL = "3890320020@qq.com"


def git_identity() -> tuple[str, str]:
    """返回 (name, email)，用于 git 提交与 PR 的作者/提交者身份。"""
    conf = load_local_conf()
    name = (conf.get("git_name") or "").strip() or DEFAULT_GIT_NAME
    email = (conf.get("git_email") or "").strip() or DEFAULT_GIT_EMAIL
    return name, email


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
