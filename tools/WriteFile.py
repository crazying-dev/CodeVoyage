"""文件写入工具（供 AI 使用）。"""
import os

from tools import ReadFile


def write_file(filepath: str, content: str) -> str:
    """写入（或覆盖）文件。filepath 为相对工作区的路径，content 为完整文件内容。"""
    try:
        target = ReadFile._resolve(filepath)
    except (ValueError, RuntimeError) as e:
        return f"Error: {e}"
    if not ReadFile.WORKSPACE:
        return "Error: workspace 未初始化"
    if os.path.abspath(target).replace("\\", "/").endswith(".git") or "/.git/" in os.path.abspath(target).replace("\\", "/"):
        return "Error: 不允许修改 .git 目录"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(content or "")
        return f"OK: 已写入 {filepath}（{len(content or '')} 字符）"
    except Exception as e:
        return f"Error: 写入失败: {e}"
