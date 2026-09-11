"""Git 仓库工具（通过本机 git CLI 执行）。

- 克隆时把 GitHub PAT 嵌入 URL 仅用于本次命令，不写进 origin 配置；PAT 绝不外传；
- 默认剔除系统代理环境变量（HTTP_PROXY/HTTPS_PROXY 等），避免被失效代理拖死；
  如需代理，设置 CODEVOYAGE_PROXY 后重启；
- 网络类命令（clone/fetch/push）失败自动重试，默认 5 次、间隔 5 秒
  （可用 CODEVOYAGE_RETRY / CODEVOYAGE_RETRY_INTERVAL 调整）。
"""
import os
import shutil
import stat
import subprocess
import time

DEFAULT_RETRY = 5
DEFAULT_RETRY_INTERVAL = 5.0

# 判定为“网络类失败”的关键字（命中才重试）
RETRYABLE_MARKERS = (
    "couldn't connect", "failed to connect", "connection refused", "connection reset",
    "timed out", "timeout", "could not resolve host", "unable to access",
    "the remote end hung up", "rpc failed", "early eof", "tls", "ssl",
    "temporary failure", "network is unreachable", "operation timed out",
    "empty reply from server", "http 5",
)


def _retry_config() -> tuple[int, float]:
    try:
        times = int(os.getenv("CODEVOYAGE_RETRY", str(DEFAULT_RETRY)))
    except ValueError:
        times = DEFAULT_RETRY
    try:
        interval = float(os.getenv("CODEVOYAGE_RETRY_INTERVAL", str(DEFAULT_RETRY_INTERVAL)))
    except ValueError:
        interval = DEFAULT_RETRY_INTERVAL
    return max(0, times), max(0.0, interval)


def _log(message: str) -> None:
    try:
        from centre import paths

        paths.append_log(message)
    except Exception:
        pass


def _git_env() -> dict:
    """git 子进程环境：默认剔除系统代理；仅当 CODEVOYAGE_PROXY 设置时启用代理。

    另外：当本机 GitHub 代理（centre.proxy）处于代理模式时，把 HTTPS 请求交给它的
    本地 CONNECT 代理，从而在直连失败时经由其它客户端代连。
    """
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    proxy = (os.getenv("CODEVOYAGE_PROXY") or "").strip()
    if not proxy:
        try:
            from centre import proxy as cv_proxy

            if cv_proxy.in_proxy_mode() and cv_proxy.active():
                proxy = cv_proxy.local_proxy_url()
        except Exception:
            proxy = ""
    if proxy:
        env["HTTP_PROXY"] = env["http_proxy"] = proxy
        env["HTTPS_PROXY"] = env["https_proxy"] = proxy
    return env


def _is_retryable(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in RETRYABLE_MARKERS)


def _run(dest: str | None, *args: str, retries: int | None = 0, label: str = "") -> str:
    """执行 git 命令。retries=None 表示用环境配置（默认 5 次 / 5 秒）。"""
    cmd = ["git"]
    if dest:
        cmd.extend(["-C", dest])
    cmd.extend(args)

    if retries is None:
        times, interval = _retry_config()
    else:
        times, interval = max(0, retries), _retry_config()[1]
    attempts = times + 1
    last_error = "git error"
    for attempt in range(1, attempts + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=_git_env())
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
        last_error = (proc.stderr or proc.stdout or "git error").strip()
        if attempt < attempts and _is_retryable(last_error):
            name = label or " ".join(args[:2])
            _log(f"git {name} 失败（第 {attempt}/{attempts} 次）：{last_error[:200]}；{interval:.0f}s 后重试")
            time.sleep(interval)
            continue
        break
    raise RuntimeError(last_error)


def _token_url(repo_full: str, token: str | None) -> str:
    """把令牌嵌入 HTTPS 克隆/推送地址（仅本次命令使用，不写入 origin）。"""
    if token:
        return f"https://x-access-token:{token}@github.com/{repo_full}.git"
    return f"https://github.com/{repo_full}.git"


def clone(repo_full: str, token: str | None, dest: str) -> None:
    """克隆仓库到 dest。传入 token 则用 x-access-token 形式访问私有/写权限仓库。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        raise RuntimeError(f"目标目录已存在: {dest}")
    _run(None, "clone", "--quiet", _token_url(repo_full, token), dest, retries=None, label="clone")


def clone_shallow(repo_full: str, token: str | None, dest: str, depth: int = 1) -> None:
    """浅克隆默认分支（只取最新提交），适合“建分支 → 提交 → 推送”的场景。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        raise RuntimeError(f"目标目录已存在: {dest}")
    _run(None, "clone", "--quiet", "--depth", str(depth), "--single-branch",
         _token_url(repo_full, token), dest, retries=None, label="clone")


def unshallow(dest: str) -> None:
    """把浅克隆补全为完整历史（推送被拒时的兜底）。"""
    _run(dest, "fetch", "--unshallow", "--quiet", retries=None, label="fetch --unshallow")


def current_branch(dest: str) -> str:
    return _run(dest, "rev-parse", "--abbrev-ref", "HEAD")


def set_identity(dest: str, name: str, email: str) -> None:
    """设置提交身份（author/committer）。"""
    _run(dest, "config", "user.name", name)
    _run(dest, "config", "user.email", email)


def write_file(dest: str, rel_path: str, content: str) -> str:
    """在工作区内写入文件（自动创建目录）。"""
    target = os.path.join(dest, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return target


def changed(dest: str, path: str) -> bool:
    """判断指定路径相对 HEAD 是否有改动（新增/修改均算）。"""
    return bool(_run(dest, "status", "--porcelain", "--", path))


def commit_paths(dest: str, paths: list, message: str) -> str:
    """只提交指定路径，返回 commit 短哈希。"""
    _run(dest, "add", "--", *paths)
    staged = _run(dest, "diff", "--cached", "--name-only")
    if not staged:
        raise RuntimeError("没有产生任何文件改动")
    _run(dest, "commit", "-m", message)
    return _run(dest, "rev-parse", "--short", "HEAD")


def create_branch(dest: str, branch: str) -> None:
    """基于当前 HEAD 创建并切换到新分支；分支已存在则重置到最新远端默认分支。"""
    try:
        _run(dest, "checkout", "-B", branch)
    except RuntimeError:
        _run(dest, "checkout", "-B", branch)


def commit_all(dest: str, message: str, name: str | None = None, email: str | None = None) -> None:
    """提交全部改动；name/email 为提交身份（默认取本地配置的提交邮箱）。"""
    _run(dest, "config", "user.name", name or "CodeVoyage AI")
    _run(dest, "config", "user.email", email or "3890320020@qq.com")
    _run(dest, "add", "-A")
    changed = _run(dest, "status", "--porcelain")
    if not changed:
        raise RuntimeError("没有产生任何文件改动")
    _run(dest, "commit", "-m", message)


def push(dest: str, repo_full: str, token: str, branch: str) -> None:
    _run(dest, "push", "--quiet", _token_url(repo_full, token), f"{branch}:{branch}",
         retries=None, label="push")


def changed_files(dest: str) -> str:
    return _run(dest, "diff", "--stat", "HEAD")


def remove_dir(dest: str) -> None:
    """强制删除目录（Windows 下 .git 内文件为只读，需先去掉只读属性）。"""
    if not dest or not os.path.exists(dest):
        return
    for root, _dirs, files in os.walk(dest):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(dest, ignore_errors=True)
