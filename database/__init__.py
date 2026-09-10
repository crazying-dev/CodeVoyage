"""数据层工具统一出口。

- db.py    ：sqlite 连接、建表与全部 CRUD / 状态机函数
- Issue.py ：Issue 相关便捷封装（兼容旧结构，直接复用 db.py 的实现）
"""
import os

# 首次导入时确保 DATABASE_URL 环境变量可被 .env 填充
database_url = os.getenv("DATABASE_URL")

from database.db import (  # noqa: E402
    init_db,
    create_user,
    get_user_by_email,
    get_user_by_id,
    verify_password,
    check_user,
    token_of,
    password_digest,
    create_repo_binding,
    get_binding_by_repo,
    get_binding_by_id,
    list_repos,
    update_repo_keywords,
    delete_repo_binding,
    create_issue,
    claim_next_issue,
    get_active_issue,
    get_issue_by_uuid,
    update_issue_result,
    list_issues,
    count_issues,
)
from database import db  # noqa: E402,F401
from database import Issue  # noqa: E402,F401

__all__ = [name for name in dir() if not name.startswith("_")]
