"""仓库操作工具（由 AI 主动调用）。

克隆仓库、建分支、提交推送、创建 PR 原本在任务开始时由程序直接执行，
现在改为工具交给 AI 按需调用，程序只负责注入上下文与兜底。

提交信息与 PR 标题统一走 centre.commit_msg（Conventional Commits，Issue #15）：
AI 给出的信息合规就原样使用，不合规则自动改写，避免历史里出现模糊/无效提交。

令牌解析仍走 paths.resolve_tokens（仓库专属细粒度 → 全局传统，顺序回退），
网络受限时经 proxy.github_call 自动切到 GitHub 代理。
"""
from centre import commit_msg, paths, proxy
from tools import GitRepo, ReadFile

_ctx = {
    "repo_full": "",
    "uid": "",
    "issue_number": None,
    "issue_title": "",
    "dest": "",
    "cloned": False,
    "token": "",
    "token_source": "",
    "branch": "",
    "default_branch": "",
    "changed_files": [],
    "pushed": False,
    "pr_url": "",
}


def set_context(repo_full: str, uid: str, issue_number, dest: str, title: str = "") -> None:
    """任务开始时注入上下文（每次任务重置状态）。title 为 Issue 标题，用于推断提交信息。"""
    _ctx.update({
        "repo_full": repo_full, "uid": uid, "issue_number": issue_number, "dest": dest,
        "issue_title": str(title or "").strip(), "cloned": False, "token": "",
        "token_source": "", "branch": "", "default_branch": "", "changed_files": [],
        "pushed": False, "pr_url": "",
    })


def state() -> dict:
    return dict(_ctx)


def _log(msg: str) -> None:
    paths.append_log(f"[{_ctx['repo_full']}#{_ctx['issue_number']}] {msg}")


def _changed_files(dest: str) -> list:
    """工作区改动文件清单（`git status --porcelain` 的首列状态 + 路径）。"""
    out = []
    for line in GitRepo.changed_files(dest).splitlines():
        path = line[3:].strip() if len(line) > 3 else ""
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if path:
            out.append(path)
    return out


# ---------------------------------------------------------------- 工具实现
def clone_repo() -> str:
    """克隆目标仓库到工作区（多令牌顺序回退）。"""
    if _ctx["cloned"]:
        return "仓库已克隆，无需重复操作。"
    repo_full = _ctx["repo_full"]
    candidates = paths.resolve_tokens(repo_full)
    if not candidates:
        return "错误：该仓库没有可用令牌，请先在控制台「配置」页添加令牌。"
    last = None
    for idx, (token, source) in enumerate(candidates, start=1):
        def _clone_once(_token=token) -> None:
            # 每次尝试前清理脏目录（上次失败可能留下半成品）；
            # 直连遇到网络类失败时，proxy.github_call 会自动切到 GitHub 代理重试。
            GitRepo.remove_dir(_ctx["dest"])
            GitRepo.clone(repo_full, _token, _ctx["dest"])

        try:
            proxy.github_call(_clone_once)
            _ctx.update({"cloned": True, "token": token, "token_source": source})
            ReadFile.set_workspace(_ctx["dest"])
            kind = "仓库专属/细粒度" if source == "repo" else "全局/传统"
            _log(f"克隆成功（第 {idx} 个令牌，来源 {kind}）")
            return f"克隆成功，工作区已就绪：{_ctx['dest']}"
        except Exception as e:
            last = e
            _log(f"第 {idx} 个令牌克隆失败：{e}")
    hint = ""
    low = str(last).lower()
    if any(k in low for k in ("connect", "timed out", "timeout", "unable to access", "network")):
        hint = "（网络类失败，已按 5 次 / 5 秒重试；如网络受限请在「配置」页开启 GitHub 代理）"
    return f"错误：全部 {len(candidates)} 个令牌都无法克隆仓库：{last}{hint}"


def create_branch() -> str:
    """基于仓库默认分支创建本次任务的分支。"""
    if not _ctx["cloned"]:
        return "错误：请先调用 clone_repo 克隆仓库。"
    if _ctx["branch"]:
        return f"分支 {_ctx['branch']} 已就绪。"
    dest = _ctx["dest"]
    default = ""
    try:
        from GithubTool import user as gh_user

        default = proxy.github_call(gh_user.default_branch, _ctx["token"], _ctx["repo_full"])
    except Exception:
        default = ""
    if not default:
        default = GitRepo.current_branch(dest)
    branch = f"codevoyage/issue-{_ctx['issue_number']}-{str(_ctx['uid'])[:6]}"
    try:
        GitRepo.create_branch(dest, branch)
    except Exception as e:
        return f"错误：创建分支失败：{e}"
    _ctx.update({"branch": branch, "default_branch": default})
    _log(f"已创建分支 {branch}（基于 {default}）")
    return f"已基于 {default} 创建并切换到分支 {branch}"


def commit_and_push(message: str = "") -> str:
    """提交工作区全部改动并推送当前分支。

    提交信息遵循 Conventional Commits（Issue #15）：`<type>(<scope>): <description>`；
    AI 给的信息不合规时会被自动改写，结果连同说明一起返回。
    """
    if not _ctx["branch"]:
        return "错误：请先调用 create_branch 创建分支。"
    dest = _ctx["dest"]
    if not GitRepo.changed_files(dest).strip():
        return "错误：工作区没有任何文件改动，无需提交。"
    files = _changed_files(dest)
    _ctx["changed_files"] = files
    name, email = paths.git_identity()
    raw = (message or "").strip() or _ctx["issue_title"] or f"处理 #{_ctx['issue_number']}"
    msg, note = commit_msg.ensure(
        raw, files=files, issue_number=_ctx["issue_number"], title=_ctx["issue_title"],
        scope_hint=commit_msg.infer_scope(files),
    )
    if note:
        _log(f"提交信息规范化：{note}")
    try:
        GitRepo.commit_all(dest, msg, name=name, email=email)
        proxy.github_call(GitRepo.push, dest, _ctx["repo_full"], _ctx["token"], _ctx["branch"])
    except Exception as e:
        return f"错误：提交或推送失败：{e}"
    _ctx["pushed"] = True
    _log(f"已提交并推送：{msg.splitlines()[0]}")
    tail = f"（{note}）" if note else ""
    return f"已提交并推送到分支 {_ctx['branch']}{tail}；提交信息：\n{msg}"


def create_pull_request(title: str = "", body: str = "") -> str:
    """调用 GitHub API 创建 Pull Request（标题同样遵循 Conventional Commits）。"""
    if not _ctx["pushed"]:
        return "错误：请先调用 commit_and_push 推送改动。"
    if _ctx["pr_url"]:
        return f"PR 已存在：{_ctx['pr_url']}"
    try:
        from GithubTool import user as gh_user

        raw_title = (title or "").strip() or _ctx["issue_title"]
        if not raw_title:
            raw_title = f"处理 #{_ctx['issue_number']}"
        pr_title = commit_msg.pr_title(
            raw_title, _ctx["issue_number"],
            files=_ctx["changed_files"] or _changed_files(_ctx["dest"]),
        )
        pr_url = proxy.github_call(
            gh_user.create_pr, _ctx["token"], _ctx["repo_full"], _ctx["branch"],
            _ctx["default_branch"], pr_title, body or "",
        )
    except Exception as e:
        return f"错误：创建 PR 失败：{e}"
    _ctx["pr_url"] = pr_url
    _log(f"已创建 PR：{pr_url}（标题 {pr_title}）")
    return f"PR 已创建：{pr_url}；标题：{pr_title}"
