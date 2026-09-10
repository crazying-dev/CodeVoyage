"""目录浏览工具（供 AI 使用）。"""
import os

from tools import ReadFile


def list_dir(path: str = ".") -> str:
    """列出工作区内目录内容。path 相对工作区，默认 '.'。"""
    try:
        target = ReadFile._resolve(path or ".")
    except (ValueError, RuntimeError) as e:
        return f"Error: {e}"
    if not os.path.isdir(target):
        return "Error: 目录不存在"
    try:
        names = sorted(os.listdir(target), key=str.lower)
    except Exception as e:
        return f"Error: {e}"
    lines = []
    for n in names:
        full = os.path.join(target, n)
        kind = "dir" if os.path.isdir(full) else "file"
        lines.append(f"{n}/" if kind == "dir" else n)
    if not lines:
        return "(空目录)"
    return "\n".join(lines)
