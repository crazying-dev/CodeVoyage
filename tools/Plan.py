"""Plan 工具：仅记录计划，不执行任何副作用。"""


def plan(text: str) -> str:
    """记录你的处理计划（text 为计划内容）。该调用不会修改任何文件。"""
    return "OK: 计划已记录。请继续按计划工作，最终答复需包含 Introduce / Body / Conclusion 三部分。"
