# 默认 LLM 模型（可在本地配置 local.json 中覆盖）
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"

# AI 在工作区内可调用的工具（每个工具单独一个文件，tools/*）
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区内文件内容，路径相对工作区，例如 'README.md' 或 'src/main.py'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "相对工作区的文件路径"}
                },
                "required": ["filepath"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入或覆盖工作区内文件，content 必须为文件完整内容，路径相对工作区。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "相对工作区的文件路径"},
                    "content": {"type": "string", "description": "文件完整内容"},
                },
                "required": ["filepath", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出工作区内某目录的内容，path 相对工作区，默认 '.'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的目录，默认 '.'"}
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "动手修改前先输出处理计划，便于审计。该调用不会修改任何文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "计划内容"}
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
]

# 名称到执行函数的映射（新增工具需同步登记）
tool_impl = {
    "read_file": "ReadFile.read_file",
    "write_file": "WriteFile.write_file",
    "list_dir": "ListDir.list_dir",
    "plan": "Plan.plan",
}
