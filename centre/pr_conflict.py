"""PR 冲突自动处理（Issue #19：自动解决 GitHub PR 冲突）。

流程（读 → 判 → 做 → 校验 → 说明）：
1. 读：GithubTool.pulls 读取 PR 状态（mergeable / mergeable_state）与改动文件；
2. 判：约束校验（见下），不满足只报告、不动作；
3. 做：本地把目标分支 merge 进 PR 源分支（**不用 rebase**，绝不 force push），
   逐个冲突文件按策略解决，无法安全判定的冲突不猜测；
4. 校验：解决后确认文件里已无冲突标记，否则整体回滚；
5. 说明：提交并推送（普通推送）后在 PR 下留言，写清楚自动解决了什么，便于人工复核。

安全约束（宁可不做，不可做错）：
- 只处理「同仓库 + codevoyage/ 工作分支」的 PR；Fork / 外部贡献者分支默认拒绝
  （allow_foreign=True 可显式放开，但仍不 force push）；
- 只处理 open、未合并、且目标分支 = 仓库默认分支的 PR；
- 冲突文件数、单文件体积、二进制文件都有硬上限，超限只报告不修改；
- 工作区必须干净（有未提交改动时拒绝），开始前记录回滚点；
- 任何一步出错、冲突标记未清零、推送失败，一律 abort + reset --hard 回到处理前状态；
- 全过程写运行日志（[PR冲突] 前缀），动作可追溯。
"""
import os
import re
from datetime import datetime

from centre import paths
from tools import GitRepo

# 允许自动写入的工作分支前缀（与 GithubTool.pulls.BRANCH_PREFIX 保持一致）
BRANCH_PREFIX = "codevoyage/"
# 一次最多自动解决多少个冲突文件（超出只报告）
MAX_CONFLICT_FILES = 20
# 单个冲突文件体积上限（超出只报告，避免把大文件改坏）
MAX_FILE_BYTES = 512 * 1024
# 支持的解决策略
STRATEGIES = ("auto", "ours", "theirs", "union")
# 冲突标记
MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
# 冲突块：<<<<<<< ours ======= theirs >>>>>>>
_HUNK = re.compile(r"<<<<<<<[^\n]*\n(.*?)=======\n(.*?)>>>>>>>[^\n]*(?:\n|$)", re.DOTALL)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    paths.append_log(f"[PR冲突] {message}")


def _pulls():
    """延迟导入 GitHub PR API，避免本模块被导入时就加载 PyGithub。"""
    from GithubTool import pulls

    return pulls


def _tokens(repo_full: str) -> list:
    return paths.resolve_tokens(repo_full)


def _first_token(repo_full: str) -> tuple[str, str]:
    """取第一个候选令牌（git fetch/push 用），返回 (token, source)。"""
    candidates = _tokens(repo_full)
    return candidates[0] if candidates else ("", "")


def _with_tokens(repo_full: str, action) -> tuple[bool, dict, str]:
    """按令牌顺序（仓库专属 → 全局）回退执行 action(token)。

    返回 (ok, data, reason)；全部失败时 ok=False，reason 为最后一次的原因。
    """
    candidates = _tokens(repo_full)
    if not candidates:
        return False, {}, "该仓库没有可用令牌"
    reason = ""
    last: dict = {}
    for token, _source in candidates:
        try:
            data = action(token) or {}
        except Exception as e:
            reason = str(e)
            continue
        last = data
        if data.get("ok"):
            return True, data, ""
        reason = data.get("reason") or reason
        if not data.get("retryable"):
            break
    return False, last, reason or "全部令牌均失败"


# ------------------------------------------------------------ 读取与约束
def pr_state(repo_full: str, pr_ref) -> dict:
    """读取 PR 状态（含冲突判定），令牌按顺序回退。"""
    ok, data, reason = _with_tokens(repo_full, lambda t: _pulls().pr_status(t, repo_full, pr_ref))
    if not ok:
        return {"ok": False, "reason": reason or "无法读取 PR 状态"}
    data["ok"] = True
    return data


def guard(detail: dict, repo_full: str, allow_foreign: bool = False) -> str:
    """自动处理的准入约束。返回拒绝原因；空字符串表示允许处理。"""
    if not detail or not detail.get("ok"):
        return (detail or {}).get("reason") or "无法读取 PR 状态"
    if detail.get("merged"):
        return "PR 已合并，无需处理冲突"
    if (detail.get("state") or "") != "open":
        return f"PR 状态为 {detail.get('state')}（非 open），不自动处理"
    base = detail.get("base") or ""
    default = detail.get("default") or ""
    if base and default and base != default:
        return f"PR 目标分支 {base} 不是仓库默认分支（{default}），为安全起见不自动处理"
    if not allow_foreign:
        head_repo = detail.get("head_repo") or ""
        if head_repo and repo_full and head_repo != repo_full:
            return f"PR 源分支位于 {head_repo}（Fork），不会向他人仓库推送"
        head = detail.get("head") or ""
        if not _pulls().is_work_branch(head, BRANCH_PREFIX):
            return (f"PR 源分支 {head} 不属于 CodeVoyage 工作分支（{BRANCH_PREFIX}*），"
                    "默认不自动处理（确需处理可显式 allow_foreign=True）")
    return ""


def inspect(repo_full: str, pr_ref="", limit: int = 10) -> dict:
    """只读查看：不传 pr_ref 列出冲突中的 open PR；传则给出单个 PR 的详情与冲突文件。"""
    repo_full = str(repo_full or "").strip()
    if not repo_full:
        return {"ok": False, "reason": "未指定仓库"}
    if pr_ref:
        detail = pr_state(repo_full, pr_ref)
        if not detail.get("ok"):
            return {"ok": False, "reason": detail.get("reason") or "无法读取 PR 状态", "detail": detail}
        files: list = []
        if detail.get("conflict") == "conflict":
            ok, data, _reason = _with_tokens(
                repo_full, lambda t: _pulls().pr_files(t, repo_full, pr_ref, limit=50))
            if ok:
                files = data.get("files") or []
        return {"ok": True, "mode": "one", "repo": repo_full, "detail": detail,
                "files": files, "guard": guard(detail, repo_full)}
    ok, data, reason = _with_tokens(
        repo_full, lambda t: _pulls().conflicting_pull_requests(t, repo_full, limit=limit))
    if not ok:
        return {"ok": False, "reason": reason or "无法读取 PR 列表"}
    return {"ok": True, "mode": "list", "repo": repo_full,
            "items": data.get("items") or [], "checked": data.get("checked", 0)}


# ------------------------------------------------------------ 冲突文本处理
def has_markers(text: str) -> bool:
    """文本里是否仍残留冲突标记（用于解决后的强校验）。"""
    for line in (text or "").splitlines():
        if line.lstrip().startswith(MARKERS):
            return True
    return False


def count_hunks(text: str) -> int:
    """冲突块数量。"""
    return len(_HUNK.findall(text or ""))


def _equal_ignoring_ws(ours: str, theirs: str) -> bool:
    return [line.strip() for line in (ours or "").splitlines()] == \
           [line.strip() for line in (theirs or "").splitlines()]


def _union(ours: str, theirs: str) -> str:
    """两边内容都保留（去重、保持出现顺序），适合追加型文件（文档、清单）。"""
    lines: list = []
    for line in (ours or "").splitlines() + (theirs or "").splitlines():
        if line not in lines:
            lines.append(line)
    return "".join(line + "\n" for line in lines)


def resolve_text(text: str, strategy: str = "auto", stats: dict | None = None) -> tuple[str, bool]:
    """解决一段带冲突标记的文本，返回 (新文本, 是否全部解决)。

    strategy='auto' 只处理「可安全判定」的冲突：
      - 一侧为空（另一侧是纯新增）→ 取有内容的一侧；
      - 两侧差异仅在空白 → 取 ours；
    其余（两侧都有实质改动）保持原样并视为未解决，避免静默丢掉别人的代码。
    """
    strategy = (strategy or "auto").strip().lower()
    if strategy not in STRATEGIES:
        strategy = "auto"
    stats = stats if stats is not None else {}
    unresolved = {"n": 0}

    def _count(key: str) -> None:
        stats[key] = stats.get(key, 0) + 1

    def _pick(ours: str, theirs: str):
        if strategy == "ours":
            _count("ours")
            return ours
        if strategy == "theirs":
            _count("theirs")
            return theirs
        if strategy == "union":
            _count("union")
            return _union(ours, theirs)
        if not ours.strip():
            _count("theirs")
            return theirs
        if not theirs.strip():
            _count("ours")
            return ours
        if _equal_ignoring_ws(ours, theirs):
            _count("whitespace")
            return ours
        unresolved["n"] += 1
        return None

    def _sub(match) -> str:
        picked = _pick(match.group(1), match.group(2))
        if picked is None:
            return match.group(0)
        return picked

    out = _HUNK.sub(_sub, text or "")
    if unresolved["n"]:
        stats["unresolved"] = stats.get("unresolved", 0) + unresolved["n"]
    return out, unresolved["n"] == 0


def _read_text(path: str) -> str:
    """按原样读取（保留 CRLF），并做二进制判定。"""
    with open(path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw:
        raise ValueError("二进制文件，无法自动解决冲突")
    return raw.decode("utf-8", "replace")


def _write_text(path: str, text: str) -> None:
    """按原样写回（newline='' 不做换行转换，避免整文件被重写）。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# ------------------------------------------------------------ 主流程
def resolve_pr(repo_full: str, workdir: str, pr_ref, strategy: str = "auto",
               dry_run: bool = False, allow_foreign: bool = False,
               comment: bool = True, name: str = "", email: str = "") -> dict:
    """自动解决某个 PR 与目标分支的冲突。

    返回 {ok, changed, reason, pr, url, branch, base, resolved, unresolved,
          commit, commented, steps, detail}
    """
    repo_full = str(repo_full or "").strip()
    workdir = str(workdir or "").strip()
    strategy = (strategy or "auto").strip().lower()
    steps: list = []

    if strategy not in STRATEGIES:
        return {"ok": False, "changed": False, "steps": steps,
                "reason": f"未知的解决策略：{strategy}（可选 {'/'.join(STRATEGIES)}）"}
    if not repo_full:
        return {"ok": False, "changed": False, "steps": steps, "reason": "未指定仓库"}
    if not workdir or not os.path.isdir(workdir):
        return {"ok": False, "changed": False, "steps": steps,
                "reason": f"工作区不可用：{workdir}"}

    detail = pr_state(repo_full, pr_ref)
    deny = guard(detail, repo_full, allow_foreign)
    if deny:
        return {"ok": False, "changed": False, "reason": deny, "detail": detail, "steps": steps}

    number = detail.get("number")
    head = detail.get("head") or ""
    base = detail.get("base") or detail.get("default") or ""
    steps.append(f"1/6 读取 PR #{number}：mergeable={detail.get('mergeable')}，"
                 f"mergeable_state={detail.get('mergeable_state')}，判定 {detail.get('conflict')}")

    if detail.get("conflict") != "conflict":
        if detail.get("conflict") == "behind" and not dry_run:
            # 落后但无冲突：交给 GitHub 的 Update branch（不产生本地合并提交）
            ok, _data, reason = _with_tokens(
                repo_full, lambda t: _pulls().update_branch(t, repo_full, number))
            if ok:
                steps.append("2/6 无冲突但落后于目标分支：已调用 GitHub Update branch")
                return {"ok": True, "changed": False, "pr": number, "url": detail.get("url", ""),
                        "branch": head, "base": base, "steps": steps,
                        "reason": "PR 与目标分支无冲突，已用 GitHub 接口更新分支"}
            steps.append(f"2/6 更新分支未成功：{reason}")
        return {"ok": True, "changed": False, "pr": number, "url": detail.get("url", ""),
                "branch": head, "base": base, "steps": steps,
                "reason": f"PR 当前没有冲突（{detail.get('conflict')}），无需处理"}

    if dry_run:
        ok, data, _reason = _with_tokens(
            repo_full, lambda t: _pulls().pr_files(t, repo_full, number, limit=50))
        files = (data.get("files") or []) if ok else []
        steps.append("2/6 dry_run：只给出计划，不做任何本地/远端改动")
        return {"ok": True, "changed": False, "dry_run": True, "pr": number,
                "url": detail.get("url", ""), "branch": head, "base": base,
                "files": files, "strategy": strategy, "steps": steps,
                "reason": (f"PR #{number} 与 {base} 存在冲突，计划以 {strategy} 策略"
                           f"合并 {base} 进 {head}（涉及 {len(files)} 个文件）")}

    token, _source = _first_token(repo_full)
    if not token:
        return {"ok": False, "changed": False, "steps": steps,
                "reason": "该仓库没有可用令牌，无法 fetch/push", "detail": detail}

    # 工作区必须干净：否则会把「未提交改动」和「冲突解决」混在一起
    try:
        dirty = GitRepo.changed_files(workdir).strip()
    except Exception as e:
        return {"ok": False, "changed": False, "steps": steps,
                "reason": f"无法读取工作区状态：{e}", "detail": detail}
    if dirty:
        return {"ok": False, "changed": False, "steps": steps, "detail": detail,
                "reason": "工作区有未提交改动，为避免与冲突解决混在一起，已拒绝本次自动合并"}

    try:
        before_branch = GitRepo.current_branch(workdir)
    except Exception:
        before_branch = ""

    resolved: list = []
    unresolved: list = []
    commit = ""
    rollback = ""
    try:
        # 2) 切到 PR 源分支（本地没有该分支时先从远端取回来）
        if before_branch != head:
            GitRepo.fetch_branch(workdir, repo_full, token, head)
            GitRepo.checkout(workdir, head)
            steps.append(f"2/6 已切换到 PR 源分支 {head}")
        else:
            steps.append(f"2/6 已在 PR 源分支 {head}")

        rollback = GitRepo.rev_parse(workdir, "HEAD")

        # 3) 把目标分支合并进源分支（不提交），收集冲突文件
        GitRepo.fetch(workdir, repo_full, token, base)
        no_conflict, _output = GitRepo.merge_no_commit(workdir, "FETCH_HEAD")
        if no_conflict:
            GitRepo.abort_merge(workdir)
            GitRepo.reset_hard(workdir, rollback)
            steps.append("3/6 本地合并无冲突（远端信息可能已过期），放弃本次合并")
            return {"ok": True, "changed": False, "pr": number, "url": detail.get("url", ""),
                    "branch": head, "base": base, "steps": steps,
                    "reason": "本地合并未出现冲突，未做任何改动（可稍后重新检查 PR 状态）"}

        conflicts = GitRepo.conflicted_files(workdir)
        if not conflicts:
            GitRepo.abort_merge(workdir)
            GitRepo.reset_hard(workdir, rollback)
            return {"ok": False, "changed": False, "steps": steps, "detail": detail,
                    "reason": "合并失败但没有识别到冲突文件，已回滚（请人工检查）"}

        if len(conflicts) > MAX_CONFLICT_FILES:
            GitRepo.abort_merge(workdir)
            GitRepo.reset_hard(workdir, rollback)
            return {"ok": False, "changed": False, "steps": steps, "detail": detail,
                    "files": conflicts,
                    "reason": (f"冲突文件 {len(conflicts)} 个，超过自动处理上限 "
                               f"{MAX_CONFLICT_FILES} 个，为防止改坏代码已回滚，请人工处理")}

        steps.append(f"3/6 合并 {base} 产生 {len(conflicts)} 个冲突文件："
                     f"{', '.join(conflicts[:5])}" + ("…" if len(conflicts) > 5 else ""))

        # 4) 逐个文件解决冲突
        for rel in conflicts:
            try:
                target = GitRepo.safe_path(workdir, rel)
            except Exception as e:
                _rollback(workdir, rollback)
                return {"ok": False, "changed": False, "steps": steps, "detail": detail,
                        "reason": f"冲突文件路径越界，已回滚：{rel}（{e}）"}
            try:
                size = os.path.getsize(target)
            except OSError as e:
                _rollback(workdir, rollback)
                return {"ok": False, "changed": False, "steps": steps, "detail": detail,
                        "reason": f"无法读取冲突文件 {rel}：{e}，已回滚"}
            if size > MAX_FILE_BYTES:
                _rollback(workdir, rollback)
                return {"ok": False, "changed": False, "steps": steps, "detail": detail,
                        "reason": (f"冲突文件 {rel} 体积 {size} 字节，超过自动处理上限 "
                                   f"{MAX_FILE_BYTES} 字节，已回滚，请人工处理")}
            try:
                text = _read_text(target)
            except ValueError as e:
                _rollback(workdir, rollback)
                return {"ok": False, "changed": False, "steps": steps, "detail": detail,
                        "reason": f"冲突文件 {rel} 无法自动解决（{e}），已回滚"}
            hunks = count_hunks(text)
            new_text, done = resolve_text(text, strategy)
            if not done:
                unresolved.append({"file": rel, "hunks": count_hunks(new_text)})
                continue
            _write_text(target, new_text)
            GitRepo.mark_resolved(workdir, rel)
            resolved.append({"file": rel, "strategy": strategy, "hunks": hunks})

        # 5) 校验：仍有未解决冲突或残留标记则整体回滚
        if unresolved:
            _rollback(workdir, rollback)
            _log(f"{repo_full} PR #{number} 存在无法安全判定的冲突，已回滚："
                 f"{', '.join(item['file'] for item in unresolved)}")
            return {"ok": False, "changed": False, "pr": number, "url": detail.get("url", ""),
                    "branch": head, "base": base, "resolved": resolved, "unresolved": unresolved,
                    "steps": steps, "detail": detail,
                    "reason": ("存在两侧都有实质改动的冲突，无法安全自动判定，已回滚到处理前状态，"
                               "请人工解决（可指定 strategy=ours/theirs/union 再试）")}

        leftovers = []
        for item in resolved:
            try:
                if has_markers(_read_text(GitRepo.safe_path(workdir, item["file"]))):
                    leftovers.append(item["file"])
            except Exception:
                leftovers.append(item["file"])
        if leftovers:
            _rollback(workdir, rollback)
            return {"ok": False, "changed": False, "pr": number, "steps": steps, "detail": detail,
                    "reason": f"解决后仍残留冲突标记（{', '.join(leftovers)}），已回滚"}

        steps.append(f"4/6 已解决 {len(resolved)} 个冲突文件（策略 {strategy}）")

        # 6) 提交并推送（普通推送，绝不 force）
        message = (f"chore(merge): 合并 {base} 解决 PR #{number} 的冲突\n\n"
                   f"由 CodeVoyage 自动完成（策略 {strategy}）；涉及文件："
                   f"{', '.join(item['file'] for item in resolved)}")
        git_name, git_email = paths.git_identity()
        commit = GitRepo.commit_merge(workdir, message,
                                      name=name or git_name, email=email or git_email)
        steps.append(f"5/6 已提交合并结果：{commit}")
        try:
            GitRepo.push(workdir, repo_full, token, head)
        except Exception as e:
            _rollback(workdir, rollback)
            _log(f"{repo_full} PR #{number} 推送失败，已回滚本地合并：{e}")
            return {"ok": False, "changed": False, "pr": number, "url": detail.get("url", ""),
                    "branch": head, "base": base, "resolved": resolved, "steps": steps,
                    "detail": detail, "reason": f"推送失败（远端未被改动）：{e}"}
        steps.append(f"6/6 已推送到 {head}（普通推送，未使用 force）")
    except Exception as e:
        # 兜底：任何意外都回到处理前的状态，绝不留半个合并在工作区里
        _rollback(workdir, rollback)
        _log(f"{repo_full} PR #{number} 冲突处理异常，已回滚：{e}")
        return {"ok": False, "changed": False, "pr": number, "url": detail.get("url", ""),
                "branch": head, "base": base, "resolved": resolved, "steps": steps,
                "detail": detail, "reason": f"冲突处理过程出错，已回滚：{e}"}
    finally:
        # 不论成败都尽量恢复到处理前的分支，避免影响调用方后续流程
        if before_branch and before_branch != head:
            try:
                GitRepo.checkout(workdir, before_branch)
            except Exception:
                pass

    commented = False
    if comment and resolved:
        body = _comment_body(number, base, head, strategy, resolved, commit)
        ok, _data, _reason = _with_tokens(
            repo_full, lambda t: _pulls().comment(t, repo_full, number, body))
        commented = bool(ok)
        steps.append(f"已在 PR #{number} 留言说明自动解决内容：{'成功' if commented else '失败'}")

    after = pr_state(repo_full, number)
    after_state = after.get("conflict", "unknown") if after.get("ok") else "未知"
    _log(f"{repo_full} PR #{number} 冲突已自动解决（{len(resolved)} 个文件，commit {commit}）")
    return {"ok": True, "changed": True, "pr": number, "url": detail.get("url", ""),
            "branch": head, "base": base, "resolved": resolved, "unresolved": [],
            "commit": commit, "commented": commented, "steps": steps, "after": after_state,
            "reason": (f"已把 {base} 合并进 {head} 并解决 {len(resolved)} 个文件的冲突，"
                       f"commit {commit} 已推送；PR 当前冲突状态：{after_state}")}


def _rollback(workdir: str, rollback: str) -> None:
    """回滚到处理前的状态（先放弃合并，再硬重置）。失败只记日志，不抛异常。"""
    try:
        GitRepo.abort_merge(workdir)
    except Exception:
        pass
    if rollback:
        try:
            GitRepo.reset_hard(workdir, rollback)
        except Exception as e:
            _log(f"回滚失败（{workdir} → {rollback}）：{e}")


def _comment_body(number, base: str, head: str, strategy: str, resolved: list, commit: str) -> str:
    """PR 留言正文：写清楚自动改了什么，便于人工复核。"""
    lines = [
        f"### CodeVoyage 自动解决冲突（PR #{number}）",
        "",
        f"- 处理时间：{_now()}",
        f"- 目标分支：`{base}` → 源分支：`{head}`",
        f"- 解决策略：`{strategy}`，提交：`{commit}`",
        f"- 改动文件（{len(resolved)} 个）：",
    ]
    lines.extend(f"  - `{item['file']}`（{item['hunks']} 处冲突）" for item in resolved)
    lines += [
        "",
        "说明：本次以 **merge（非 rebase）** 方式同步目标分支，未使用 force push；"
        "仅自动解决了可安全判定的冲突（一侧为空 / 仅空白差异 / 指定的 ours·theirs·union 策略）。",
        "如对某处取舍有疑问，请在本 PR 中直接指出或回退该提交。",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------ 文本输出
def format_inspect(result: dict) -> str:
    """把 inspect 的返回值整理成给 AI / 人看的文本。"""
    if not result.get("ok"):
        return result.get("reason") or "无法读取 PR 信息"
    lines = []
    if result.get("mode") == "list":
        items = result.get("items") or []
        lines.append(f"仓库 {result.get('repo')}：与默认分支冲突的 open PR 共 {len(items)} 个"
                     f"（已补查 mergeable 状态 {result.get('checked', 0)} 个）。")
        for it in items[:20]:
            lines.append(f"  - #{it.get('number')} {it.get('title')}"
                         f"（{it.get('head')} → {it.get('base')}，"
                         f"mergeable_state={it.get('mergeable_state')}）{it.get('url')}")
        if not items:
            lines.append("  （当前没有冲突中的 PR）")
        lines.append("提示：处理某个 PR 请调用 resolve_pr_conflicts(pr=<编号或链接>)。")
        return "\n".join(lines)

    detail = result.get("detail") or {}
    lines.append(f"PR #{detail.get('number')}：{detail.get('title')}")
    lines.append(f"  - 链接：{detail.get('url')}")
    lines.append(f"  - 分支：{detail.get('head')} → {detail.get('base')}"
                 f"（默认分支 {detail.get('default')}，源仓库 {detail.get('head_repo')}）")
    lines.append(f"  - 状态：{detail.get('state')}{'（草稿）' if detail.get('draft') else ''}"
                 f"，merged={detail.get('merged')}")
    lines.append(f"  - 可合并：mergeable={detail.get('mergeable')}，"
                 f"mergeable_state={detail.get('mergeable_state')}，判定={detail.get('conflict')}")
    files = result.get("files") or []
    if files:
        lines.append(f"  - 涉及文件（{len(files)}）：")
        for f in files[:20]:
            lines.append(f"    - {f.get('filename')}（{f.get('status')}，"
                         f"+{f.get('additions')}/-{f.get('deletions')}）")
    lines.append(f"  - 约束：{result.get('guard') or '通过（可调用 resolve_pr_conflicts 自动解决）'}")
    return "\n".join(lines)


def format_result(result: dict) -> str:
    """把 resolve_pr 的返回值整理成给 AI / 人看的文本。"""
    lines = []
    if result.get("pr"):
        lines.append(f"PR #{result['pr']}：{result.get('url', '')}")
    if result.get("reason"):
        lines.append(result["reason"])
    if result.get("dry_run"):
        lines.append("（dry_run：只输出计划，未改动任何文件与远端）")
    if result.get("resolved"):
        lines.append("已解决：")
        for item in result["resolved"]:
            lines.append(f"  - {item['file']}：{item['strategy']}（{item['hunks']} 处冲突）")
    if result.get("unresolved"):
        lines.append("未能安全解决（已回滚，未改动任何内容）：")
        for item in result["unresolved"]:
            lines.append(f"  - {item['file']}：{item.get('hunks', 0)} 处冲突需人工判断")
    if result.get("commit"):
        lines.append(f"已提交并推送：{result['commit']} → {result.get('branch')}（未使用 force push）")
    if result.get("commented"):
        lines.append("已在 PR 下留言说明本次自动解决的内容，便于人工复核。")
    if result.get("steps"):
        lines.append("步骤：")
        lines.extend(f"  - {step}" for step in result["steps"])
    return "\n".join(lines) if lines else "无操作"
