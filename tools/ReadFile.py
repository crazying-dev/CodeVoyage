"""文件读取工具（供 AI 使用）。

工作区由 Agent 引擎在开始任务前通过 set_workspace 注入；
路径安全校验：只允许读取工作区内部文件，越权直接拒绝。
"""
import os

WORKSPACE = ""


def set_workspace(path: str) -> None:
    global WORKSPACE
    WORKSPACE = os.path.abspath(path)


def _resolve(filepath: str) -> str:
    if not WORKSPACE:
        raise RuntimeError("workspace 未初始化")
    if os.path.isabs(filepath):
        raise ValueError("不支持绝对路径")
    target = os.path.abspath(os.path.join(WORKSPACE, filepath))
    if not target.startswith(os.path.abspath(WORKSPACE) + os.sep) and target != os.path.abspath(WORKSPACE):
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
