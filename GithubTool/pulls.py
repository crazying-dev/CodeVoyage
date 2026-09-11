"""Pull Request 读取与评论（通知工作流，Issue #20）。

GitHub 的评论接口对 Issue 与 PR 是同一套：PR 在 REST API 里就是一种 Issue，
`repo.get_issue(n).create_comment(...)` 既能给 Issue 回帖，也能给 PR 回帖。
因此本模块既是「PR 提交后自动回复」的落地实现，也支撑「PR 事件回执」：
任务结束后由 centre.pr_notify 调用这里，把结果同步到 Issue / PR 上。

边界与安全：
- 只读取 + 评论，绝不合并 / 关闭 / 修改任何内容；
- 令牌由调用方从本机解析后传入（paths.resolve_tokens），不落盘、不外传；
- 失败一律翻译成可操作提示（user.friendly_error），由调用方决定是否换令牌重试。
"""
import re

from GithubTool import user as gh_user

PR_URL_RE = re.compile(r"/pull/(\d+)")
MAX_COMMENT_LEN = 6000      # 单条评论长度上限（GitHub 上限约 65536，这里留足余量）


def pr_number(pr_ref) -> int:
    """从 PR 编号或 PR 链接解析编号，解析失败返回 0。"""
    text = str(pr_ref or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    m = PR_URL_RE.search(text)
    return int(m.group(1)) if m else 0


def pr_detail(token: str, repo_full: str, pr_ref) -> dict:
    """读取 PR 概要，用于通知内容与状态判断（只读，不改动任何内容）。

    返回 {ok, number, title, state, draft, merged, mergeable, html_url,
          head, base, author, changed_files, additions, deletions, reason}
    """
    number = pr_number(pr_ref)
    if not number:
        return {"ok": False, "number": 0, "reason": f"无法解析 PR 编号：{pr_ref}"}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        pr = repo.get_pull(number)
        return {
            "ok": True,
            "number": pr.number,
            "title": pr.title or "",
            "state": pr.state,                                   # open / closed
            "draft": bool(getattr(pr, "draft", False)),
            "merged": bool(getattr(pr, "merged", False)),
            "mergeable": getattr(pr, "mergeable", None),
            "html_url": pr.html_url,
            "head": (getattr(getattr(pr, "head", None), "ref", "") or ""),
            "base": (getattr(getattr(pr, "base", None), "ref", "") or ""),
            "author": (getattr(getattr(pr, "user", None), "login", "") or ""),
            "changed_files": int(getattr(pr, "changed_files", 0) or 0),
            "additions": int(getattr(pr, "additions", 0) or 0),
            "deletions": int(getattr(pr, "deletions", 0) or 0),
            "reason": "",
        }
    except Exception as e:
        return {"ok": False, "number": number, "reason": gh_user.friendly_error(e)}


def comment(token: str, repo_full: str, number, body: str) -> dict:
    """在 Issue / PR 上回帖（通知）。返回 {ok, url, reason}。"""
    try:
        n = int(number)
    except (TypeError, ValueError):
        return {"ok": False, "url": "", "reason": f"编号非法：{number}"}
    text = str(body or "").strip()
    if not n or not text:
        return {"ok": False, "url": "", "reason": "编号或评论内容为空"}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        created = repo.get_issue(n).create_comment(text[:MAX_COMMENT_LEN])
    except Exception as e:
        return {"ok": False, "url": "", "reason": gh_user.friendly_error(e)}
    return {"ok": True, "url": (getattr(created, "html_url", "") or ""), "reason": ""}


def has_marker(token: str, repo_full: str, number, marker: str, limit: int = 50) -> bool:
    """该 Issue / PR 下是否已有带标记的评论（去重，避免重复通知刷屏）。

    只扫最近 limit 条；读取失败一律当作「没有」，交给调用方按令牌顺序回退。
    """
    text = str(marker or "").strip()
    if not text:
        return False
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        issue = repo.get_issue(int(number))
        for index, item in enumerate(issue.get_comments()):
            if text in (getattr(item, "body", "") or ""):
                return True
            if index + 1 >= max(1, int(limit)):
                break
    except Exception:
        return False
    return False
