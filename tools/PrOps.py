"""PR 相关工具（由 AI / 程序主动调用，Issue #19）。

两个工具：
- pr_conflicts           只读：不传 pr 时列出「与默认分支冲突」的 open PR；
                         传 pr（编号或链接）时给出该 PR 的可合并状态与涉及文件；
- resolve_pr_conflicts  动作：自动解决 PR 与目标分支的冲突
                         （merge 目标分支 → 按策略解决冲突 → 校验 → 提交推送 → PR 留言）。

安全约束全部在 centre.pr_conflict 中实现：只处理同仓库的 codevoyage/* 工作分支、
绝不 force push、工作区不干净或无法安全判定时一律回滚且不改动远端。
"""
from centre import pr_conflict
from tools import RepoOps


def _repo(repo: str) -> str:
    """仓库取值：显式传入优先，其次当前任务上下文。"""
    text = str(repo or "").strip()
    if text:
        return text
    return (RepoOps.state().get("repo_full") or "").strip()


def pr_conflicts(pr: str = "", repo: str = "", limit: int = 10) -> str:
    """查看 PR 冲突情况（只读，不做任何修改）。

    pr 为空：列出当前仓库中「合并进默认分支且有冲突」的 open PR；
    pr 为编号或链接：输出该 PR 的 mergeable 状态、涉及文件与准入约束结论。
    """
    repo_full = _repo(repo)
    if not repo_full:
        return "错误：未指定仓库，且当前任务没有仓库上下文（请传入 repo=owner/name）"
    try:
        limit = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError):
        limit = 10
    result = pr_conflict.inspect(repo_full, str(pr or "").strip(), limit=limit)
    if not result.get("ok"):
        return f"错误：{result.get('reason') or '无法读取 PR 信息'}"
    return pr_conflict.format_inspect(result)


def resolve_pr_conflicts(pr: str, strategy: str = "auto", dry_run: bool = False,
                         allow_foreign: bool = False, repo: str = "") -> str:
    """自动解决某个 PR 的冲突（会提交并推送到 PR 源分支）。

    pr            必填：PR 编号或链接
    strategy      解决策略：auto（默认，只解决可安全判定的冲突）/ ours / theirs / union
    dry_run       True 只输出计划，不改动任何内容
    allow_foreign 是否允许处理非 codevoyage/* 的源分支（默认 False，Fork 永不处理）
    """
    repo_full = _repo(repo)
    if not repo_full:
        return "错误：未指定仓库，且当前任务没有仓库上下文（请传入 repo=owner/name）"
    state = RepoOps.state()
    workdir = state.get("dest", "")
    if not workdir:
        return "错误：当前任务没有可用的工作区，请先调用 clone_repo 克隆仓库"
    result = pr_conflict.resolve_pr(
        repo_full, workdir, str(pr or "").strip(), strategy=strategy,
        dry_run=bool(dry_run), allow_foreign=bool(allow_foreign),
    )
    text = pr_conflict.format_result(result)
    if result.get("ok"):
        return text
    return f"错误：{text}"
