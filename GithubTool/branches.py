"""GitHub 工作分支相关 API（Issue #13：PR 合并后自动销毁工作分支）。

只做三件事，且全部是「读多写少 + 强校验」的安全操作：
- pr_detail：查询某个 PR 是否已合并进默认分支（判断能否销毁分支的唯一依据）；
- delete_branch：删除远端 CodeVoyage 工作分支，只允许 codevoyage/ 前缀，
  默认分支与受保护名称一律拒绝，避免误删；
- merged_work_branches：列出「PR 已合并到默认分支」的工作分支，
  作为待清理队列之外的兜底发现（覆盖历史遗留 / 其它机器创建的分支）。

令牌与网络策略复用 GithubTool.user.gh（代理 / 直连自适应）。
"""
import re

from GithubTool import user as gh_user

# 允许自动销毁的分支前缀（CodeVoyage 只创建这一类工作分支）
BRANCH_PREFIX = "codevoyage/"

# 永不自动删除的分支名（即使前缀命中，也直接拒绝）
PROTECTED_NAMES = {
    "main", "master", "develop", "development", "dev", "trunk",
    "release", "staging", "production", "gh-pages",
}


def _status(err) -> int | None:
    """从异常里取 HTTP 状态码（PyGithub 异常带 status，回退到文本匹配）。"""
    status = getattr(err, "status", None)
    if status:
        try:
            return int(status)
        except (TypeError, ValueError):
            pass
    m = re.search(r"\b(401|403|404|422)\b", str(err))
    return int(m.group(1)) if m else None


def pr_number(pr_ref) -> int:
    """从 PR 编号或 PR 链接中解析编号，解析失败返回 0。"""
    text = str(pr_ref or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    m = re.search(r"/pull/(\d+)", text)
    return int(m.group(1)) if m else 0


def pr_detail(token: str, repo_full: str, pr_ref) -> dict:
    """查询 PR 状态。

    返回 {ok, number, url, state, merged, head, base, default, reason, retryable}
      - merged：是否已合并（GitHub 只在 PR 合并后置 true）；
      - head / base / default：源分支、目标分支、仓库默认分支，用于二次校验。
    """
    number = pr_number(pr_ref)
    if not number:
        return {"ok": False, "reason": f"无法解析 PR 编号：{pr_ref}", "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        pr = repo.get_pull(number)
        head_obj = getattr(pr, "head", None)
        base_obj = getattr(pr, "base", None)
        head_repo = getattr(head_obj, "repo", None)
        return {
            "ok": True,
            "number": pr.number,
            "url": pr.html_url,
            "state": pr.state,                      # open / closed
            "merged": bool(getattr(pr, "merged", False)),
            "head": (getattr(head_obj, "ref", "") or ""),
            "base": (getattr(base_obj, "ref", "") or ""),
            "head_repo": (getattr(head_repo, "full_name", "") or ""),
            "default": repo.default_branch,
            "reason": "",
            "retryable": False,
        }
    except Exception as e:
        return {"ok": False, "reason": gh_user.friendly_error(e),
                "retryable": gh_user.is_retryable(e)}


def delete_branch(token: str, repo_full: str, branch: str, prefix: str = BRANCH_PREFIX) -> dict:
    """删除远端工作分支。返回 {ok, deleted, reason, retryable}。

    多重保护（宁可不删，不可误删）：
    - 分支名必须命中 CodeVoyage 工作分支前缀，其它分支一律拒绝；
    - 默认分支、受保护名称、release/*、hotfix/* 一律拒绝；
    - 分支已不存在（404）视为成功（无需再删）。
    """
    name = str(branch or "").strip()
    if not name:
        return {"ok": False, "deleted": False, "reason": "分支名为空", "retryable": False}
    if prefix and not name.startswith(prefix):
        return {"ok": False, "deleted": False,
                "reason": f"分支 {name} 不属于 CodeVoyage 工作分支（{prefix}*），为安全起见不自动删除",
                "retryable": False}
    low = name.lower()
    if low in PROTECTED_NAMES or low.startswith(("release/", "hotfix/")):
        return {"ok": False, "deleted": False,
                "reason": f"受保护分支 {name} 不允许自动删除", "retryable": False}
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        if name == repo.default_branch:
            return {"ok": False, "deleted": False, "reason": "默认分支不允许删除", "retryable": False}
        repo.get_git_ref(f"heads/{name}").delete()
    except Exception as e:
        if _status(e) == 404:
            return {"ok": True, "deleted": False, "reason": "分支已不存在（无需删除）", "retryable": False}
        return {"ok": False, "deleted": False, "reason": gh_user.friendly_error(e),
                "retryable": gh_user.is_retryable(e)}
    return {"ok": True, "deleted": True, "reason": "", "retryable": False}


def merged_work_branches(token: str, repo_full: str, prefix: str = BRANCH_PREFIX,
                         limit: int = 100) -> dict:
    """列出「PR 已合并进默认分支」的工作分支（兜底发现用）。

    返回 {ok, branches: [{branch, pr, url}], default, reason, retryable}
    - 只看目标分支 = 仓库默认分支的已合并 PR；
    - 只取本仓库（非 Fork）创建的分支，避免误删他人仓库的同名分支。
    """
    try:
        repo = gh_user.gh(token).get_repo(repo_full)
        default = repo.default_branch
        out = []
        pulls = repo.get_pulls(state="closed", base=default, sort="updated", direction="desc")
        for pr in pulls[:limit]:
            if not getattr(pr, "merged", False):
                continue
            head_obj = getattr(pr, "head", None)
            head = (getattr(head_obj, "ref", "") or "")
            if not head.startswith(prefix):
                continue
            head_repo = getattr(head_obj, "repo", None)
            head_full = getattr(head_repo, "full_name", "") or ""
            if head_full and head_full != repo_full:
                continue
            out.append({"branch": head, "pr": pr.number, "url": pr.html_url})
        return {"ok": True, "branches": out, "default": default, "reason": "", "retryable": False}
    except Exception as e:
        return {"ok": False, "branches": [], "reason": gh_user.friendly_error(e),
                "retryable": gh_user.is_retryable(e)}
