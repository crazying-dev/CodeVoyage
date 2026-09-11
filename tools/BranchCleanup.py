"""工作分支清理工具（由 AI / 控制台调用）。

CodeVoyage 的工作分支（codevoyage/*）在 PR 合并进默认分支后不再需要保留。
真正的自动销毁由 centre.branch_gc 的后台巡检完成（Agent 空闲时每 10 分钟一次，
任务创建 PR 后登记分支，合并后即删除）；本工具用于查询待清理队列或立即巡检一次。

安全性：销毁动作完全复用 centre.branch_gc，只删 codevoyage/* 且 PR 已合并进默认
分支的分支，默认分支与受保护分支永不删除。
"""
from centre import branch_gc


def _fmt(items: list, limit: int = 20) -> list:
    lines = []
    for it in items[:limit]:
        lines.append(f"  - {it.get('repo', '')} {it.get('branch', '')}：{it.get('note', '')}")
    return lines


def cleanup_branches(run: bool = False, repo: str = "") -> str:
    """查看或清理 PR 已合并的工作分支。

    run=False：只列出待清理队列（不修改任何远端内容）；
    run=True ：立即巡检并销毁「PR 已合并进默认分支」的 codevoyage/* 分支。
    repo 为空表示处理本机全部仓库。
    """
    repo = str(repo or "").strip()
    if not run:
        info = branch_gc.summary(repo)
        lines = [f"待清理队列：共 {info['total']} 条记录，其中 {info['pending']} 条待处理"
                 f"（已销毁 {info['deleted']} 条）。"]
        lines += _fmt(info["items"])
        lines.append("提示：工作分支会在 PR 合并进默认分支后由后台巡检自动销毁；"
                     "需要立即清理请调用 cleanup_branches(run=true)。")
        return "\n".join(lines)

    result = branch_gc.sweep(repo_full=repo)
    lines = [
        f"巡检完成：扫描仓库 {result['repos']} 个，检查分支记录 {result['checked']} 条；"
        f"销毁 {len(result['deleted'])} 个，保留 {len(result['kept'])} 个，失败 {len(result['failed'])} 个。"
    ]
    if result["deleted"]:
        lines.append("已销毁：")
        lines += _fmt(result["deleted"])
    if result["kept"]:
        lines.append("保留（PR 未合并或非默认分支，不做破坏性操作）：")
        lines += _fmt(result["kept"])
    if result["failed"]:
        lines.append("失败：")
        lines += _fmt(result["failed"])
    return "\n".join(lines)
