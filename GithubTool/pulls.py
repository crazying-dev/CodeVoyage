"""Pull Request 读取与动作 API（Issue #19：自动处理 PR 冲突）。

职责边界（读多写少，写动作全部带约束）：
- 读：list_pull_requests / pr_status / pr_files / conflicting_pull_requests
      —— PR 列表、可合并状态（mergeable / mergeable_state）、改动文件；
- 写：update_branch（用 base 更新 head，等价于 GitHub 界面的 Update branch）、
      comment（在 PR 下留言）、merge（仅在可合并且非草稿时）；
- 冲突判定统一约定：mergeable=False 或 mergeable_state == 'dirty' 表示存在冲突；
  GitHub 首次查询可能返回 null（后台还在算），此时按 unknown 处理，由调用方重试或稍后再看。

安全：本模块不提供任何强制写动作（没有 force push、不改默认分支内容），
写动作的合法性判断集中在 centre.pr_conflict 的约束里（只处理同仓库 + codevoyage/* 分支）。
"""
import time

from GithubTool import branches as gh_branches
from GithubTool import user as gh_user

# CodeVoyage 工作分支前缀（只有这类分支才允许自动写入）
BRANCH_PREFIX = "codevoyage/"

# 表示「与目标分支有冲突」的 mergeable_state
CONFLICT_STATES = ("dirty",)

# mergeable 为未知（GitHub 计算中）时的重查次数与间隔
REFRESH_ATTEMPTS = 3
REFRESH_INTERVAL = 1.5


def _safe_get(obj, name, default=None):
    """读取属性；属性不存在或延迟加载失败时返回默认值（不抛异常）。"""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def pr_number(pr_ref) -> int:
    """从 PR 编号或链接解析编号（复用 branches 的实现）。"""
    return gh_branches.pr_number(pr_ref)


def _brief(pr) -> dict:
    """把 PR 对象压成只含关键字段的字典。"""
    head = _safe_get(pr, "head")
    base = _safe_get(pr, "base")
    head_repo = _safe_get(head, "repo")
    return {
        "number": _safe_get(pr, "number", 0) or 0,
        "title": _safe_get(pr, "title", "") or "",
        "url": _safe_get(pr, "html_url", "") or "",
        "state": _safe_get(pr, "state", "") or "",
        "draft": bool(_safe_get(pr, "draft", False)),
        "merged": bool(_safe_get(pr, "merged", False)),
        "head": _safe_get(head, "ref", "") or "",
        "base": _safe_get(base, "ref", "") or "",
        "head_repo": _safe_get(head_repo, "full_name", "") or "",
        "mergeable": _safe_get(pr, "mergeable", None),
        "mergeable_state": _safe_get(pr, "mergeable_state", "") or "",
        "updated_at": str(_safe_get(pr, "updated_at", "") or ""),
    }


def conflict_state(info: dict) -> str:
    """把 PR 状态归纳成 clean / conflict / behind / unknown。

    - conflict：mergeable=False 或 mergeable_state='dirty'（存在冲突，必须更新分支或解冲突）；
    - behind  ：可合并但落后于目标分支；
    - unknown ：GitHub 还没算出 mergeable（null），需要稍后重查。
    """
    if info.get("mergeable") is False or (info.get("mergeable_state") or "") in CONFLICT_STATES:
        return "conflict"
    if info.get("mergeable") is None:
        return "unknown"
    if (info.get("mergeable_state") or "") == "behind":
        return "behind"
    return "clean"


def pr_status(token: str, repo_full: str, pr_ref,
              attempts: int = REFRESH_ATTEMPTS, interval: float = REFRESH_INTERVAL) -> dict:
    """查询单个 PR 的状态（含是否冲突）。

    返回 {ok, number, title, url, state, draft, merged, head, base, head_repo, default,
          mergeable, mergeable_state, conflict, reason, retryable}
    """
    number = pr_number(pr_ref)
    if not number:
        return {"ok": False, "reason": f"无法解析 PR 编号：{pr_ref}", "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        default = repo.default_branch
        info: dict = {}
        for attempt in range(1, max(1, int(attempts)) + 1):
            info = _brief(repo.get_pull(number))
            info["default"] = default
            # 已关闭/已合并的 PR 不会再有 mergeable；未知状态最多重查 attempts 次
            if info.get("mergeable") is not None or info.get("state") != "open":
                break
            if attempt < attempts:
                time.sleep(interval)
        info.update({"ok": True, "reason": "", "retryable": False, "conflict": conflict_state(info)})
        return info
    except Exception as e:
        return {"ok": False, "reason": gh_user.friendly_error(e), "retryable": gh_user.is_retryable(e)}


def pr_files(token: str, repo_full: str, pr_ref, limit: int = 100) -> dict:
    """列出 PR 的改动文件（用于冲突排查与人工复核）。"""
    number = pr_number(pr_ref)
    if not number:
        return {"ok": False, "files": [], "reason": f"无法解析 PR 编号：{pr_ref}", "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        pr = repo.get_pull(number)
        files = []
        for f in pr.get_files()[: max(1, int(limit))]:
            files.append({
                "filename": _safe_get(f, "filename", "") or "",
                "status": _safe_get(f, "status", "") or "",
                "additions": _safe_get(f, "additions", 0) or 0,
                "deletions": _safe_get(f, "deletions", 0) or 0,
                "changes": _safe_get(f, "changes", 0) or 0,
            })
        return {"ok": True, "files": files, "reason": "", "retryable": False}
    except Exception as e:
        return {"ok": False, "files": [], "reason": gh_user.friendly_error(e),
                "retryable": gh_user.is_retryable(e)}


def list_pull_requests(token: str, repo_full: str, state: str = "open",
                       limit: int = 20, base: str = "") -> dict:
    """列出 PR（默认只取 open，按更新时间倒序）。"""
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        pulls = repo.get_pulls(state=state, sort="updated", direction="desc", base=base or None)
        items = [_brief(pr) for pr in pulls[: max(1, int(limit))]]
        for it in items:
            it["conflict"] = conflict_state(it)
        return {"ok": True, "items": items, "default": repo.default_branch,
                "reason": "", "retryable": False}
    except Exception as e:
        return {"ok": False, "items": [], "reason": gh_user.friendly_error(e),
                "retryable": gh_user.is_retryable(e)}


def conflicting_pull_requests(token: str, repo_full: str, limit: int = 20,
                              check: int = 10, base: str = "") -> dict:
    """列出「合并进默认分支且存在冲突」的 open PR。

    列表接口对 mergeable 可能返回 null，这里对状态未知的 PR 最多补查 check 个，
    避免为了一个列表把 API 调用打满。
    """
    res = list_pull_requests(token, repo_full, state="open", limit=limit, base=base)
    if not res.get("ok"):
        return res
    default = res.get("default") or ""
    out: list = []
    checked = 0
    for info in res.get("items", []):
        when_base = info.get("base") or ""
        if default and when_base and when_base != default:
            continue                      # 只关心合并进默认分支的 PR
        state = info.get("conflict") or conflict_state(info)
        if state == "unknown" and checked < max(0, int(check)):
            checked += 1
            detail = pr_status(token, repo_full, info.get("number"))
            if detail.get("ok"):
                info = dict(info, mergeable=detail.get("mergeable"),
                            mergeable_state=detail.get("mergeable_state"))
                state = detail.get("conflict", state)
        if state == "conflict":
            out.append(dict(info, conflict="conflict"))
    return {"ok": True, "items": out, "default": default, "checked": checked,
            "reason": "", "retryable": False}


def is_work_branch(branch: str, prefix: str = BRANCH_PREFIX) -> bool:
    """是否 CodeVoyage 自己的工作分支（自动写入的唯一合法对象）。"""
    return str(branch or "").strip().startswith(prefix)


def update_branch(token: str, repo_full: str, pr_ref, expected_head_sha: str = "") -> dict:
    """用 base 更新 head（GitHub 官方 Update branch 接口）。

    - 只在 PR 与目标分支无冲突时可用，冲突时 GitHub 返回 422（本次由本地合并解决）；
    - expected_head_sha 传入时，远端 head 与它不一致会返回 422（避免覆盖别人的新提交）。
    """
    number = pr_number(pr_ref)
    if not number:
        return {"ok": False, "reason": f"无法解析 PR 编号：{pr_ref}", "retryable": False}
    payload = {}
    if expected_head_sha:
        payload["expected_head_sha"] = str(expected_head_sha)
    try:
        from centre import net

        resp = net.request(
            "put",
            f"https://api.github.com/repos/{repo_full}/pulls/{number}/update-branch",
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=payload,
        )
    except Exception as e:
        try:
            from centre import net as _net

            return {"ok": False, "reason": _net.friendly_error(e), "retryable": True}
        except Exception:
            return {"ok": False, "reason": str(e)[:200], "retryable": True}
    if resp.status_code == 202:
        body = {}
        try:
            body = resp.json() if resp.text else {}
        except ValueError:
            body = {}
        return {"ok": True, "message": body.get("message", ""), "reason": "", "retryable": False}
    if resp.status_code == 422:
        return {"ok": False, "retryable": False,
                "reason": "该 PR 无法用 base 更新分支（通常是存在冲突或不允许自动更新）"}
    return {"ok": False, "retryable": resp.status_code in (401, 403, 404),
            "reason": f"更新分支失败（HTTP {resp.status_code}）：{(resp.text or '')[:200]}"}


def comment(token: str, repo_full: str, pr_ref, body: str) -> dict:
    """在 PR 下留言（自动处理结果的可复核记录）。"""
    number = pr_number(pr_ref)
    if not number:
        return {"ok": False, "reason": f"无法解析 PR 编号：{pr_ref}", "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        pr = repo.get_pull(number)
        pr.create_issue_comment(body or "")
        return {"ok": True, "reason": "", "retryable": False}
    except Exception as e:
        return {"ok": False, "reason": gh_user.friendly_error(e), "retryable": gh_user.is_retryable(e)}


def merge(token: str, repo_full: str, pr_ref, method: str = "merge") -> dict:
    """合并 PR（仅在可合并、非草稿时执行；调用方负责判断）。"""
    number = pr_number(pr_ref)
    if not number:
        return {"ok": False, "reason": f"无法解析 PR 编号：{pr_ref}", "retryable": False}
    if method not in ("merge", "squash", "rebase"):
        return {"ok": False, "reason": f"不支持的合并方式：{method}", "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        pr = repo.get_pull(number)
        status = pr.merge(merge_method=method)
        merged = bool(_safe_get(status, "merged", False))
        if merged:
            return {"ok": True, "merged": True, "reason": "", "retryable": False}
        return {"ok": False, "merged": False, "retryable": False,
                "reason": _safe_get(status, "message", "") or "PR 未能合并"}
    except Exception as e:
        return {"ok": False, "merged": False, "reason": gh_user.friendly_error(e),
                "retryable": gh_user.is_retryable(e)}
