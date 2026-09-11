"""工作分支自动销毁（Issue #13）。

背景：CodeVoyage 每次任务都会新建 codevoyage/issue-* 工作分支并提交 PR。
PR 合并后如果不清理，仓库分支列表会不断堆积。本模块负责「PR 合并进默认分支后
自动销毁工作分支」，既保证仓库可维护性，又绝不误删尚未合并的改动。

两条路径：
- 队列：任务成功创建 PR 后由 Agent 登记（仓库 + 分支 + PR），巡检时精确判断并删除；
- 兜底发现：本地有数据的仓库，直接列出「PR 已合并进默认分支」的工作分支并删除，
  覆盖历史遗留或在本机之外创建的工作分支。

安全约束：只处理 codevoyage/ 前缀；默认分支与受保护分支永不删除；PR 未合并、
PR 关闭但未合并、合并目标不是默认分支时一律保留分支；查询/删除失败只记失败状态，
绝不影响 Agent 主流程。每次实际销毁都写入运行日志，便于审计。

数据落盘：~/.CodeVoyage/Agent/repo/<repo_hash>/branches.json
"""
import os
import threading
import time
from datetime import datetime

from centre import paths

BRANCH_PREFIX = "codevoyage/"
SWEEP_INTERVAL = 600.0   # 秒：后台巡空间隔（默认 10 分钟一次）
MAX_RECORDS = 200        # 每个仓库最多保留的记录条数
# 需要每轮重新判定的状态：pending（刚登记）/ failed（上次出错）/ kept（PR 还没合并）
RETRY_STATES = ("pending", "failed", "kept")

_lock = threading.RLock()
_last_sweep = 0.0


def _gh():
    """延迟导入 GitHub 分支 API，避免本模块被导入时就加载 PyGithub。"""
    from GithubTool import branches

    return branches


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------ 队列读写 ------------------------------
def _file(repo_full: str) -> str:
    return os.path.join(paths.repo_dir(repo_full), "branches.json")


def load(repo_full: str) -> list:
    """读取某仓库的待销毁记录（不存在或损坏时返回空列表）。"""
    items = paths.load_json(_file(repo_full), [])
    return items if isinstance(items, list) else []


def save(repo_full: str, items: list) -> None:
    try:
        os.makedirs(os.path.dirname(_file(repo_full)), exist_ok=True)
        paths.save_json(_file(repo_full), list(items)[:MAX_RECORDS])
    except Exception as e:
        paths.append_log(f"[分支销毁] 记录写入失败 {repo_full}: {e}")


def register(repo_full: str, branch: str, pr_url: str = "", source: str = "issue") -> dict | None:
    """登记待销毁的工作分支（PR 合并后由巡检自动删除）。

    非 codevoyage/ 前缀的分支直接忽略，保证不会把普通分支放进销毁队列。
    """
    branch = str(branch or "").strip()
    if not repo_full or not branch.startswith(BRANCH_PREFIX):
        return None
    number = _gh().pr_number(pr_url)
    with _lock:
        items = load(repo_full)
        for it in items:
            if it.get("branch") == branch:
                it["pr_url"] = pr_url or it.get("pr_url", "")
                it["pr_number"] = number or it.get("pr_number", 0)
                it["updated_at"] = _now()
                save(repo_full, items)
                return it
        record = {
            "repo_full": repo_full,
            "branch": branch,
            "pr_url": pr_url,
            "pr_number": number,
            "source": source,          # issue / workflow（其它登记来源）
            "status": "pending",       # pending / kept / deleted / failed
            "note": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        items.insert(0, record)
        save(repo_full, items)
    paths.append_log(f"[分支销毁] 已登记 {repo_full} {branch}（PR 合并后自动删除）")
    return record


def summary(repo_full: str = "") -> dict:
    """待销毁队列概览（供控制台 / AI 工具查看）。"""
    repos = [repo_full] if repo_full else _repos_to_sweep()
    items = []
    for repo in repos:
        for it in load(repo):
            items.append(dict(it, repo_full=repo))
    pending = [i for i in items if i.get("status") in RETRY_STATES]
    deleted = [i for i in items if i.get("status") == "deleted"]
    return {"total": len(items), "pending": len(pending), "deleted": len(deleted),
            "repos": repos, "items": items[:100]}


# ------------------------------ 巡检 ------------------------------
def _repos_to_sweep() -> list:
    """本地有数据的仓库（来自各仓库目录下的 Info.json）。"""
    out = []
    base = paths.REPO_DIR
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        folder = os.path.join(base, name)
        if not os.path.isdir(folder):
            continue
        repo_full = (paths.load_json(os.path.join(folder, "Info.json"), {}) or {}).get("repo_full", "")
        if repo_full:
            out.append(repo_full)
    return out


def _run_with_tokens(repo_full: str, action) -> tuple[bool, dict, str]:
    """按令牌顺序（仓库专属 → 全局，即 paths.resolve_tokens）回退执行 action(token)。

    返回 (ok, data, reason)；全部失败时 ok=False，reason 为最后一次的原因。
    """
    candidates = paths.resolve_tokens(repo_full)
    if not candidates:
        return False, {}, "该仓库没有可用令牌"
    reason = ""
    for token, _source in candidates:
        try:
            data = action(token) or {}
        except Exception as e:
            reason = str(e)
            continue
        if data.get("ok"):
            return True, data, ""
        reason = data.get("reason") or reason
        if not data.get("retryable"):
            break
    return False, {}, reason or "全部令牌均失败"


def _destroy(repo_full: str, item: dict) -> tuple[str, str]:
    """判断单条记录能否销毁并执行。返回 (状态, 说明)。"""
    branch = item.get("branch") or ""
    pr_ref = item.get("pr_number") or item.get("pr_url")
    if not pr_ref:
        return "kept", "记录里没有 PR 信息，保留分支待人工确认"
    ok, detail, reason = _run_with_tokens(
        repo_full, lambda t: _gh().pr_detail(t, repo_full, pr_ref))
    if not ok:
        return "failed", f"查询 PR 失败：{reason}"
    if not detail.get("merged"):
        if detail.get("state") == "closed":
            return "kept", "PR 已关闭但未合并，保留分支（不做破坏性操作）"
        return "kept", "PR 尚未合并，保留分支等待合并"
    if detail.get("base") != detail.get("default"):
        return "kept", f"PR 合并目标是 {detail.get('base')}（非默认分支），保留分支待人工确认"
    if detail.get("head") and detail.get("head") != branch:
        return "kept", f"PR 源分支为 {detail.get('head')}，与记录不一致，保留分支待人工确认"
    ok, res, reason = _run_with_tokens(
        repo_full, lambda t: _gh().delete_branch(t, repo_full, branch))
    if not ok:
        return "failed", f"删除分支失败：{reason}"
    if res.get("deleted"):
        return "deleted", "PR 已合并进默认分支，工作分支已销毁"
    return "deleted", res.get("reason") or "分支已不存在"


def _discover_and_destroy(repo_full: str, limit: int, result: dict, seen: set) -> None:
    """兜底发现：列出该仓库中 PR 已合并进默认分支的工作分支并销毁。"""
    ok, data, reason = _run_with_tokens(
        repo_full, lambda t: _gh().merged_work_branches(t, repo_full, BRANCH_PREFIX))
    if not ok:
        # 无令牌 / 无权限 / 网络不可用：记入 notes 并写日志，不当作失败刷屏
        result["notes"].append({"repo": repo_full, "note": f"兜底发现跳过：{reason}"})
        paths.append_log(f"[分支销毁] {repo_full} 兜底发现跳过：{reason}")
        return
    for found in data.get("branches", []):
        branch = str(found.get("branch") or "").strip()
        if not branch or (repo_full, branch) in seen:
            continue
        if result["checked"] >= limit * 2:
            break
        result["checked"] += 1
        seen.add((repo_full, branch))
        entry = {"repo": repo_full, "branch": branch, "pr": found.get("url", ""),
                 "note": f"PR #{found.get('pr')} 已合并"}
        ok2, res, reason2 = _run_with_tokens(
            repo_full, lambda t: _gh().delete_branch(t, repo_full, branch))
        if ok2:
            entry["note"] += f"，{res.get('reason') or '工作分支已销毁'}"
            result["deleted"].append(entry)
            paths.append_log(f"[分支销毁] {repo_full} {branch}：已销毁（兜底发现，PR #{found.get('pr')} 已合并）")
        else:
            entry["note"] += f"，删除失败：{reason2}"
            result["failed"].append(entry)
            paths.append_log(f"[分支销毁] {repo_full} {branch}：删除失败（{reason2}）")


def sweep(deep: bool = True, limit: int = 50, repo_full: str = "") -> dict:
    """巡检一轮：销毁「PR 已合并进默认分支」的工作分支。

    deep=True 时额外做兜底发现（列出仓库里所有已合并的 codevoyage/* PR 分支）。
    pending/failed/kept 状态每轮都会重新判定（kept = 上次 PR 还没合并，需持续等待）。
    返回 {checked, deleted, kept, failed, notes, repos}，全过程只记日志、不抛异常。
    """
    result = {"checked": 0, "deleted": [], "kept": [], "failed": [], "notes": [], "repos": 0}
    repos = [repo_full] if repo_full else _repos_to_sweep()
    seen: set = set()
    for repo in repos:
        result["repos"] += 1
        items = load(repo)
        changed = False
        for item in items:
            if item.get("status") not in RETRY_STATES:
                continue
            if result["checked"] >= limit:
                break
            status, note = _destroy(repo, item)
            result["checked"] += 1
            item.update({"status": status, "note": note, "checked_at": _now(), "updated_at": _now()})
            changed = True
            seen.add((repo, item.get("branch")))
            result[{"deleted": "deleted", "kept": "kept"}.get(status, "failed")].append(
                {"repo": repo, "branch": item.get("branch"), "pr": item.get("pr_url", ""), "note": note}
            )
            paths.append_log(f"[分支销毁] {repo} {item.get('branch')}：{status}（{note}）")
        if changed:
            save(repo, items)
        if deep:
            _discover_and_destroy(repo, limit, result, seen)
    return result


def sweep_due(interval: float = SWEEP_INTERVAL, force: bool = False) -> dict:
    """节流巡检（供后台线程调用，默认 10 分钟一次；force=True 立即执行）。"""
    global _last_sweep
    with _lock:
        now = time.time()
        if not force and now - _last_sweep < interval:
            return {"skipped": True, "checked": 0, "deleted": [], "kept": [],
                    "failed": [], "notes": [], "repos": 0}
        _last_sweep = now
    try:
        result = sweep()
    except Exception as e:  # 巡检绝不能把 Agent 主循环带崩
        paths.append_log(f"[分支销毁] 巡检异常：{e}")
        return {"skipped": False, "checked": 0, "deleted": [], "kept": [],
                "failed": [{"note": str(e)}], "notes": [], "repos": 0}
    result["skipped"] = False
    if result["deleted"] or result["failed"]:
        paths.append_log(
            f"[分支销毁] 巡检完成：仓库 {result['repos']} 个，检查 {result['checked']} 条，"
            f"销毁 {len(result['deleted'])}，保留 {len(result['kept'])}，失败 {len(result['failed'])}"
        )
    return result
