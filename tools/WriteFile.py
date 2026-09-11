"""文件写入工具（供 AI 使用）。

安全约束（与 ReadFile 保持一致）：
- 路径必须是工作区内的相对路径，绝对路径 / `..` / 符号链接逃逸一律拒绝；
- 不允许写入 `.git` 目录，避免污染版本库元数据。
"""
import os

from tools import ReadFile


def _in_git_dir(target: str) -> bool:
    parts = os.path.normcase(os.path.abspath(target)).replace("\\", "/").split("/")
    return ".git" in parts


def write_file(filepath: str, content: str) -> str:
    """写入（或覆盖）文件。filepath 为相对工作区的路径，content 为完整文件内容。"""
    if not ReadFile.WORKSPACE:
        return "错误：workspace 未初始化"
    try:
        target = ReadFile._resolve(filepath)
    except (ValueError, RuntimeError) as e:
        return f"错误：{e}"
    if _in_git_dir(target):
        return "错误：不允许修改 .git 目录"
    try:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 目录可能是软链接：创建后再校验一次，防止写入工作区之外
        if not ReadFile._inside(target, ReadFile.WORKSPACE):
            return "错误：路径超出工作区，已拒绝"
        with open(target, "w", encoding="utf-8") as f:
            f.write(content or "")
        return f"OK: 已写入 {filepath}（{len(content or '')} 字符）"
    except Exception as e:
        return f"错误：写入失败：{e}"
