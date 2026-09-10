"""Issue 数据访问（保留模块结构，实现收敛在 database/db.py）。"""
from database.db import (
    create_issue,
    claim_next_issue,
    get_active_issue,
    get_issue_by_uuid,
    update_issue_result,
    list_issues,
    count_issues,
)
