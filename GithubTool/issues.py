"""Issue 读取与回复 API（Issue #20：通知工作流）。

- 读：`issue_detail` —— 标题 / 状态 / 链接 / 是否 PR，用于通知前后核对目标；
- 写：`comment` —— 在 Issue 下回复一条评论。GitHub 的 PR 评论与 Issue 评论是同一套
  接口，所以自动回复 PR 也用它。

边界：本模块只提供「留言」这一种写动作，不提供关闭 / 重开 / 加标签 / 锁定等会改变
Issue 状态的能力 —— 自动通知不能替项目维护者做决定。令牌与网络策略复用
`GithubTool.user.gh`（代理 / 直连自适应），错误文案统一走 `friendly_error`。
"""
import re

from GithubTool import user as gh_user


def issue_number(issue_ref) -> int:
    """从编号或链接里解析 Issue / PR 编号（解析失败返回 0）。"""
    text = str(issue_ref or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    m = re.search(r"/(?:issues|pull)/(\d+)", text)
    return int(m.group(1)) if m else 0


def issue_detail(token: str, repo_full: str, issue_ref) -> dict:
    """读取 Issue（或 PR）基本信息。

    返回 {ok, number, title, state, url, is_pr, reason, retryable}
    """
    number = issue_number(issue_ref)
    if not number:
        return {"ok": False, "reason": f"无法解析 Issue 编号：{issue_ref}", "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        issue = repo.get_issue(number)
        return {
            "ok": True,
            "number": issue.number,
            "title": (getattr(issue, "title", "") or ""),
            "state": (getattr(issue, "state", "") or ""),
            "url": (getattr(issue, "html_url", "") or ""),
            "is_pr": bool(getattr(issue, "pull_request", None)),
            "reason": "",
            "retryable": False,
        }
    except Exception as e:
        return {"ok": False, "number": number, "title": "", "state": "", "url": "",
                "is_pr": False, "reason": gh_user.friendly_error(e),
                "retryable": gh_user.is_retryable(e)}


def comment(token: str, repo_full: str, issue_ref, body: str) -> dict:
    """在 Issue / PR 下回复一条评论。

    返回 {ok, number, url, reason, retryable}；url 为评论链接，便于在本地轨迹里回溯。
    """
    number = issue_number(issue_ref)
    if not number:
        return {"ok": False, "number": 0, "url": "",
                "reason": f"无法解析 Issue 编号：{issue_ref}", "retryable": False}
    if not str(body or "").strip():
        return {"ok": False, "number": number, "url": "", "reason": "回复内容为空",
                "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        created = repo.get_issue(number).create_comment(str(body))
        return {"ok": True, "number": number, "url": (getattr(created, "html_url", "") or ""),
                "reason": "", "retryable": False}
    except Exception as e:
        return {"ok": False, "number": number, "url": "",
                "reason": gh_user.friendly_error(e), "retryable": gh_user.is_retryable(e)}
