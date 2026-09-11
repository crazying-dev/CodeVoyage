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
    {
        "type": "function",
        "function": {
            "name": "clone_repo",
            "description": "克隆本次 Issue 所属仓库到工作区。开始任何文件读写前必须先调用（会自动按顺序尝试可用令牌）。",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_branch",
            "description": "基于仓库默认分支创建并切换到本次任务的分支。clone_repo 之后、修改文件之前调用。",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_and_push",
            "description": "提交工作区全部改动并推送到当前分支。改动完成后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "提交信息，一句话说明改动"}
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pull_request",
            "description": "调用 GitHub API 创建 Pull Request，是任务的最后一步；推送成功后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "PR 标题"},
                    "body": {"type": "string", "description": "PR 正文，Markdown，说明改了什么"},
                },
                "required": ["title", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cleanup_branches",
            "description": "查看或清理 CodeVoyage 工作分支（codevoyage/*）：PR 合并进默认分支后分支会被后台巡检自动销毁；此工具用于查看待清理队列或立即清理一次。只删已合并的工作分支，默认分支永不删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "run": {"type": "boolean", "description": "false（默认）只查看待清理队列；true 立即巡检并销毁已合并的工作分支"},
                    "repo": {"type": "string", "description": "限定仓库 owner/name，留空表示本机全部仓库"},
                },
                "required": [],
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
    "clone_repo": "RepoOps.clone_repo",
    "create_branch": "RepoOps.create_branch",
    "commit_and_push": "RepoOps.commit_and_push",
    "create_pull_request": "RepoOps.create_pull_request",
    "cleanup_branches": "BranchCleanup.cleanup_branches",
}
