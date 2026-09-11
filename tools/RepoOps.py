"""仓库操作工具（由 AI 主动调用）。

克隆仓库、建分支、提交推送、创建 PR 原本在任务开始时由程序直接执行，
现在改为工具交给 AI 按需调用，程序只负责注入上下文与兜底。

令牌解析仍走 paths.resolve_tokens（仓库专属细粒度 → 全局传统，顺序回退），
网络受限时经 proxy.github_call 自动切到 GitHub 代理。
"""
from centre import paths, proxy
from tools import GitRepo, ReadFile

_ctx = {
    "repo_full": "",
    "uid": "",
    "issue_number": None,
    "dest": "",
    "cloned": False,
    "token": "",
    "token_source": "",
    "branch": "",
    "default_branch": "",
    "pushed": False,
    "pr_url": "",
}


def set_context(repo_full: str, uid: str, issue_number, dest: str) -> None:
    """任务开始时注入上下文（每次任务重置状态）。"""
    _ctx.update({
        "repo_full": repo_full, "uid": uid, "issue_number": issue_number, "dest": dest,
        "cloned": False, "token": "", "token_source": "", "branch": "",
        "default_branch": "", "pushed": False, "pr_url": "",
    })


def state() -> dict:
    return dict(_ctx)


def _log(msg: str) -> None:
    paths.append_log(f"[{_ctx['repo_full']}#{_ctx['issue_number']}] {msg}")


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
    """提交工作区全部改动并推送当前分支。"""
    if not _ctx["branch"]:
        return "错误：请先调用 create_branch 创建分支。"
    dest = _ctx["dest"]
    if not GitRepo.changed_files(dest).strip():
        return "错误：工作区没有任何文件改动，无需提交。"
    name, email = paths.git_identity()
    msg = (message or "").strip() or f"CodeVoyage: 处理 #{_ctx['issue_number']}"
    try:
        GitRepo.commit_all(dest, msg, name=name, email=email)
        proxy.github_call(GitRepo.push, dest, _ctx["repo_full"], _ctx["token"], _ctx["branch"])
    except Exception as e:
        return f"错误：提交或推送失败：{e}"
    _ctx["pushed"] = True
    _log(f"已提交并推送：{msg[:80]}")
    return f"已提交并推送到分支 {_ctx['branch']}；提交信息：{msg[:120]}"


def create_pull_request(title: str = "", body: str = "") -> str:
    """调用 GitHub API 创建 Pull Request。"""
    if not _ctx["pushed"]:
        return "错误：请先调用 commit_and_push 推送改动。"
    if _ctx["pr_url"]:
        return f"PR 已存在：{_ctx['pr_url']}"
    try:
        from GithubTool import user as gh_user

        pr_title = (title or "").strip()[:100] or f"CodeVoyage: 处理 #{_ctx['issue_number']}"
        pr_url = proxy.github_call(
            gh_user.create_pr, _ctx["token"], _ctx["repo_full"], _ctx["branch"],
            _ctx["default_branch"], pr_title, body or "",
        )
    except Exception as e:
        return f"错误：创建 PR 失败：{e}"
    _ctx["pr_url"] = pr_url
    _log(f"已创建 PR：{pr_url}")
    return f"PR 已创建：{pr_url}"
