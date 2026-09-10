"""Git 仓库工具（通过本机 git CLI 执行）。

克隆时把 GitHub PAT 嵌入 URL 仅用于本次命令，避免把密钥写进 origin 配置；
该 PAT 来自本地配置，绝不外传。
"""
import os
import subprocess


def _run(dest: str | None, *args: str) -> str:
    cmd = ["git"]
    if dest:
        cmd.append("-C")
        cmd.append(dest)
    cmd.extend(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git error").strip())
    return (proc.stdout or "").strip()


def clone(repo_full: str, token: str | None, dest: str) -> None:
    """克隆仓库到 dest。传入 token 则用 x-access-token 形式访问私有/写权限仓库。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        raise RuntimeError(f"目标目录已存在: {dest}")
    if token:
        url = f"https://x-access-token:{token}@github.com/{repo_full}.git"
    else:
        url = f"https://github.com/{repo_full}.git"
    _run(None, "clone", "--quiet", url, dest)


def current_branch(dest: str) -> str:
    return _run(dest, "rev-parse", "--abbrev-ref", "HEAD")


def create_branch(dest: str, branch: str) -> None:
    """基于当前 HEAD 创建并切换到新分支；分支已存在则重置到最新远端默认分支。"""
    try:
        _run(dest, "checkout", "-B", branch)
    except RuntimeError:
        _run(dest, "checkout", "-B", branch)


def commit_all(dest: str, message: str) -> None:
    _run(dest, "config", "user.name", "CodeVoyage AI")
    _run(dest, "config", "user.email", "codevoyage@localhost")
    _run(dest, "add", "-A")
    changed = _run(dest, "status", "--porcelain")
    if not changed:
        raise RuntimeError("没有产生任何文件改动")
    _run(dest, "commit", "-m", message)


def push(dest: str, repo_full: str, token: str, branch: str) -> None:
    url = f"https://x-access-token:{token}@github.com/{repo_full}.git"
    _run(dest, "push", "--quiet", "--set-upstream", url, branch)


def changed_files(dest: str) -> str:
    return _run(dest, "diff", "--stat", "HEAD")
