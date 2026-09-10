"""Git 仓库工具（通过本机 git CLI 执行）。

克隆时把 GitHub PAT 嵌入 URL 仅用于本次命令，避免把密钥写进 origin 配置；
该 PAT 来自本地配置，绝不外传。
"""
import os
import shutil
import stat
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
    _run(None, "clone", "--quiet", _token_url(repo_full, token), dest)


def clone_shallow(repo_full: str, token: str | None, dest: str, depth: int = 1) -> None:
    """浅克隆默认分支（只取最新提交），适合“建分支 → 提交 → 推送”的场景。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        raise RuntimeError(f"目标目录已存在: {dest}")
    _run(None, "clone", "--quiet", "--depth", str(depth), "--single-branch",
         _token_url(repo_full, token), dest)


def unshallow(dest: str) -> None:
    """把浅克隆补全为完整历史（推送被拒时的兜底）。"""
    _run(dest, "fetch", "--unshallow", "--quiet")


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
    _run(dest, "push", "--quiet", _token_url(repo_full, token), f"{branch}:{branch}")


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
