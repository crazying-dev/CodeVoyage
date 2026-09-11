"""Git 仓库工具（通过本机 git CLI 执行）。

- 克隆时把 GitHub PAT 嵌入 URL 仅用于本次命令，不写进 origin 配置；PAT 绝不外传；
- 默认剔除系统代理环境变量（HTTP_PROXY/HTTPS_PROXY 等），避免被失效代理拖死；
  如需代理，设置 CODEVOYAGE_PROXY 后重启；
- 网络类命令（clone/fetch/push）失败自动重试，默认 5 次、间隔 5 秒
  （可用 CODEVOYAGE_RETRY / CODEVOYAGE_RETRY_INTERVAL 调整）；
- 所有按“相对路径”操作文件的函数（write_file / changed / commit_paths）都经
  `_safe_join` 强制校验，杜绝 `..`、绝对路径与符号链接逃逸。

PR 冲突处理（Issue #19）用到的能力集中在文件末尾：「取分支 → 试合并（不提交）→
列出冲突文件 → 取某一侧内容 → 提交 / 回滚」。这些函数一律不做 force push，
也不改写远端历史，出问题时可调用 abort_merge / reset_hard 回到操作前的状态。
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


# ---------------------------------------------------------------- 路径安全
def _safe_join(dest: str, rel_path: str) -> str:
    """把相对路径拼到工作区内，并做强制越界校验。

    拒绝：空路径 / 含 `\\x00`、绝对路径（含盘符、UNC、`~`）、任何一段为 `..`、
    以及 realpath 归一化后跑到工作区之外的路径（符号链接逃逸）。
    """
    base = os.path.realpath(os.path.abspath(str(dest or "")))
    if not str(dest or "").strip():
        raise ValueError("工作区路径为空，已拒绝")
    if not os.path.isdir(base):
        raise RuntimeError(f"工作区不存在: {dest}")
    raw = str(rel_path or "").strip()
    if not raw:
        raise ValueError("路径不能为空")
    if "\x00" in raw:
        raise ValueError("路径包含非法字符")
    normalized = raw.replace("\\", "/")
    drive, _tail = os.path.splitdrive(normalized)
    if drive or normalized.startswith("/") or normalized.startswith("~"):
        raise ValueError("不支持绝对路径")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"路径包含 .. 越权参数，已拒绝：{rel_path}")
    target = os.path.realpath(os.path.join(base, *parts)) if parts else base
    base_cmp, target_cmp = os.path.normcase(base), os.path.normcase(target)
    if target_cmp != base_cmp and not target_cmp.startswith(base_cmp + os.sep):
        raise ValueError(f"路径超出工作区，已拒绝：{rel_path}")
    return target


def safe_path(dest: str, rel_path: str) -> str:
    """对外暴露的路径校验（PR 冲突处理按冲突文件路径落盘时复用同一套约束）。"""
    return _safe_join(dest, rel_path)


def _rel(dest: str, rel_path: str) -> str:
    """校验并转成相对工作区的 git 路径。"""
    target = _safe_join(dest, rel_path)
    return os.path.relpath(target, os.path.realpath(os.path.abspath(dest)))


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
    """在工作区内写入文件（自动创建目录）。越界路径抛 ValueError / RuntimeError。"""
    target = _safe_join(dest, rel_path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return target


def changed(dest: str, path: str) -> bool:
    """判断指定路径相对 HEAD 是否有改动（新增/修改均算）。"""
    target = _safe_join(dest, path)
    rel = os.path.relpath(target, os.path.realpath(os.path.abspath(dest)))
    return bool(_run(dest, "status", "--porcelain", "--", rel))


def commit_paths(dest: str, paths: list, message: str) -> str:
    """只提交指定路径，返回 commit 短哈希。"""
    rels = []
    for path in paths or []:
        target = _safe_join(dest, path)
        rels.append(os.path.relpath(target, os.path.realpath(os.path.abspath(dest))))
    if not rels:
        raise ValueError("未指定要提交的路径")
    _run(dest, "add", "--", *rels)
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
    """工作区相对 HEAD 的改动清单（含新增的未跟踪文件）。

    不能用 `git diff HEAD`：它只统计已跟踪文件，新建文件是未跟踪状态，
    不会出现在 diff 里，从而被误判为「没有任何文件改动」。
    """
    return _run(dest, "status", "--porcelain")


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


# ------------------------------------------------- PR 冲突处理（Issue #19）
def fetch(dest: str, repo_full: str, token: str, *refs: str) -> str:
    """从远端取引用（不带 refs 时只更新 FETCH_HEAD / 远端跟踪引用）。"""
    args = ["fetch", "--quiet", _token_url(repo_full, token)]
    args.extend(refs)
    return _run(dest, *args, retries=None, label="fetch")


def fetch_branch(dest: str, repo_full: str, token: str, branch: str,
                 local_ref: str = "") -> str:
    """把远端 branch 取到本地引用。

    该分支正被 checkout 时不能直接更新其本地引用（git 会拒绝），因此调用方要么先切走，
    要么用 local_ref 指定别的引用名。
    """
    target = local_ref or f"refs/heads/{branch}"
    return _run(dest, "fetch", "--quiet", _token_url(repo_full, token),
                f"+refs/heads/{branch}:{target}", retries=None, label="fetch branch")


def checkout(dest: str, branch: str) -> str:
    """切换分支（分支必须已存在；工作区是否干净由调用方保证）。"""
    return _run(dest, "checkout", "--quiet", branch)


def rev_parse(dest: str, ref: str = "HEAD") -> str:
    return _run(dest, "rev-parse", ref)


def reset_hard(dest: str, ref: str = "HEAD") -> str:
    """把工作区硬重置到指定提交（回滚点，仅在冲突处理失败时使用）。"""
    return _run(dest, "reset", "--hard", ref)


def _has_conflicts(dest: str) -> bool:
    try:
        return bool(conflicted_files(dest))
    except RuntimeError:
        return False


def merge_no_commit(dest: str, ref: str) -> tuple[bool, str]:
    """把 ref 合并进当前分支，但**不自动提交**（保留解冲突的机会）。

    返回 (是否无冲突, 输出)。有冲突时返回 (False, 输出) 且工作区处于合并中状态，
    调用方必须显式地解决冲突并提交，或调用 abort_merge 放弃。
    非冲突类失败（例如 ref 不存在）抛 RuntimeError。
    """
    cmd = ["git", "-C", dest, "merge", "--no-commit", "--no-ff", ref]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=_git_env())
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return True, out
    if _has_conflicts(dest):
        return False, out
    raise RuntimeError(out or f"git merge {ref} 失败")


def conflicted_files(dest: str) -> list:
    """处于冲突状态的文件（相对工作区的路径）。"""
    out = _run(dest, "diff", "--name-only", "--diff-filter=U")
    return [line.strip() for line in out.splitlines() if line.strip()]


def take_side(dest: str, rel_path: str, side: str) -> str:
    """冲突文件整体取某一侧内容（side ∈ {'ours','theirs'}）。"""
    if side not in ("ours", "theirs"):
        raise ValueError("side 只能是 ours 或 theirs")
    return _run(dest, "checkout", f"--{side}", "--", _rel(dest, rel_path))


def mark_resolved(dest: str, rel_path: str) -> str:
    """把解决后的冲突文件标记为已解决（git add）。"""
    return _run(dest, "add", "--", _rel(dest, rel_path))


def abort_merge(dest: str) -> bool:
    """放弃进行中的合并（没有合并在进行时返回 False，不抛异常）。"""
    try:
        _run(dest, "merge", "--abort")
        return True
    except RuntimeError:
        try:
            _run(dest, "reset", "--hard", "HEAD")
        except RuntimeError:
            pass
        return False


def commit_merge(dest: str, message: str, name: str | None = None,
                 email: str | None = None) -> str:
    """提交合并结果（含冲突解决），返回 commit 短哈希。"""
    _run(dest, "config", "user.name", name or "CodeVoyage AI")
    _run(dest, "config", "user.email", email or "3890320020@qq.com")
    _run(dest, "add", "-A")
    staged = _run(dest, "diff", "--cached", "--name-only")
    if not staged:
        raise RuntimeError("合并后没有产生任何改动")
    _run(dest, "commit", "-m", message)
    return _run(dest, "rev-parse", "--short", "HEAD")
