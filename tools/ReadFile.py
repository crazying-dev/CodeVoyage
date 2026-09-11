"""文件读取工具（供 AI 使用）。

工作区由 Agent 引擎在开始任务前通过 set_workspace 注入；
路径安全校验：只允许读取工作区内部文件，越权直接拒绝。

校验分三层（缺一不可）：
1. set_workspace：工作区自身必须是绝对路径，且（给定 root 时）必须落在 root 内；
2. _resolve：拒绝绝对路径 / 盘符 / UNC / `~` / 空路径 / 含 `\x00`，逐段拒绝 `..`；
3. realpath 归一化后再用公共前缀判定，符号链接指向工作区外同样拒绝。
"""
import os

WORKSPACE = ""
WORKSPACE_ROOT = ""


def _norm(path: str) -> str:
    """realpath + normcase，用于跨平台安全比较。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))


def _inside(path: str, root: str) -> bool:
    """path 是否等于 root 或位于 root 内部。"""
    p, r = _norm(path), _norm(root)
    if p == r:
        return True
    return p.startswith(r if r.endswith(os.sep) else r + os.sep)


def set_workspace(path: str, root: str = "") -> None:
    """设置工作区；path 为空表示清理（任务结束时调用）。

    root 为允许的工作区根目录（例如 CodeVoyage 数据目录下的 Agent/repo），
    传空则只校验绝对路径。传入越界路径直接抛 ValueError，避免把工具根指到任意目录。
    """
    global WORKSPACE, WORKSPACE_ROOT
    if not path:
        WORKSPACE, WORKSPACE_ROOT = "", ""
        return
    raw = str(path).strip()
    if "\x00" in raw:
        raise ValueError("工作区路径包含非法字符")
    target = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isabs(target):
        raise ValueError("工作区必须是绝对路径")
    root_abs = os.path.abspath(os.path.expanduser(str(root))) if root else ""
    if root_abs and not _inside(target, root_abs):
        raise ValueError(f"工作区必须在 {root_abs} 内，已拒绝")
    WORKSPACE = target
    WORKSPACE_ROOT = root_abs


def workspace() -> str:
    return WORKSPACE


def _resolve(filepath: str) -> str:
    """把相对路径解析为工作区内的绝对路径；任何越权尝试都抛 ValueError。"""
    if not WORKSPACE:
        raise RuntimeError("workspace 未初始化")
    raw = str(filepath or "").strip()
    if not raw:
        raise ValueError("路径不能为空")
    if "\x00" in raw:
        raise ValueError("路径包含非法字符")
    # 统一分隔符：Windows 下 ../ 与 ..\ 等价，必须先归一化再判定
    normalized = raw.replace("\\", "/")
    drive, _tail = os.path.splitdrive(normalized)
    if drive or normalized.startswith("/") or normalized.startswith("~"):
        raise ValueError("不支持绝对路径")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("路径包含 .. 越权参数，已拒绝")
    ws = _norm(WORKSPACE)
    target = os.path.realpath(os.path.join(ws, *parts)) if parts else ws
    if not _inside(target, ws):
        raise ValueError("路径超出工作区，已拒绝")
    return target


def read_file(filepath: str) -> str:
    """读取文件内容。filepath 为相对工作区的路径，例如 'README.md' 或 'src/main.py'。"""
    try:
        target = _resolve(filepath)
    except (ValueError, RuntimeError) as e:
        return f"错误：{e}"
    if not os.path.exists(target):
        return "错误：文件不存在"
    if os.path.isdir(target):
        return "错误：目标为目录，请使用 list_dir"
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"错误：读取失败：{e}"
