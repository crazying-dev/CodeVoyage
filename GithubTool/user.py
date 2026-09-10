"""GitHub API 封装（PAT 由本地配置注入，绝不硬编码/上传）。

同时支持两类令牌：
- 传统令牌（classic）：ghp_ / gho_ / ghu_ / ghs_ / ghr_ 前缀，按 scope 授权；
- 细粒度令牌（fine-grained）：github_pat_ 前缀，按仓库 + 权限项授权。
"""
import time

import requests
from github import Github
from github.Auth import Token

CLASSIC_PREFIXES = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_")
FINE_GRAINED_PREFIX = "github_pat_"

TOKEN_HINTS = {
    "classic": [
        "传统令牌需要 scope：repo（私有仓库读写）",
        "若要提交/修改 .github/workflows 下的文件，还需勾选 workflow",
        "只读公开仓库可只勾 public_repo",
    ],
    "fine_grained": [
        "细粒度令牌必须勾选目标仓库（Repository access）",
        "权限需包含：Contents = Read and write、Pull requests = Read and write",
        "修改 .github/workflows 还需要：Workflows = Read and write",
        "Metadata = Read（默认自动包含）",
    ],
    "unknown": [
        "未识别到令牌类型，建议使用 ghp_ 开头的传统令牌或 github_pat_ 开头的细粒度令牌",
    ],
}


def token_kind(token: str) -> str:
    """按前缀判断令牌类型：classic / fine_grained / unknown。"""
    token = str(token or "").strip()
    if token.startswith(FINE_GRAINED_PREFIX):
        return "fine_grained"
    if token.startswith(CLASSIC_PREFIXES):
        return "classic"
    return "unknown"


def gh(token: str) -> Github:
    return Github(auth=Token(token))


def default_branch(token: str, repo_full: str) -> str:
    repo = gh(token).get_repo(repo_full)
    return repo.default_branch


def create_pr(token: str, repo_full: str, head: str, base: str, title: str, body: str) -> str:
    """创建 Pull Request，返回 html_url。"""
    repo = gh(token).get_repo(repo_full)
    pr = repo.create_pull(title=title, body=body, head=head, base=base)
    return pr.html_url


def whoami(token: str) -> str:
    return gh(token).get_user().login


def describe_token(token: str) -> dict:
    """校验令牌并返回类型 / 账号 / 传统令牌的 scope。

    返回 {ok, kind, login, scopes, reason}
    """
    kind = token_kind(token)
    try:
        resp = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
    except Exception as e:
        return {"ok": False, "kind": kind, "login": "", "scopes": [], "reason": f"网络请求失败：{e}"}
    if resp.status_code != 200:
        return {"ok": False, "kind": kind, "login": "", "scopes": [],
                "reason": f"令牌校验失败（HTTP {resp.status_code}）：{(resp.text or '')[:200]}"}
    data = resp.json() if resp.text else {}
    scopes = [s.strip() for s in (resp.headers.get("x-oauth-scopes") or "").split(",") if s.strip()]
    return {"ok": True, "kind": kind, "login": data.get("login", ""), "scopes": scopes, "reason": ""}


def repo_access(token: str, repo_full: str) -> dict:
    """检查令牌对指定仓库的访问权限（细粒度令牌的关键校验）。

    返回 {ok, default_branch, private, archived, can_pull, can_push, can_admin, reason, retryable}
    """
    try:
        repo = gh(token).get_repo(repo_full)
    except Exception as e:
        return {"ok": False, "reason": friendly_error(e), "retryable": is_retryable(e)}
    perms = getattr(repo, "permissions", None)
    return {
        "ok": True,
        "default_branch": repo.default_branch,
        "private": bool(repo.private),
        "archived": bool(repo.archived),
        "can_pull": bool(getattr(perms, "pull", False)) if perms else None,
        "can_push": bool(getattr(perms, "push", False)) if perms else None,
        "can_admin": bool(getattr(perms, "admin", False)) if perms else None,
        "reason": "",
        "retryable": False,
    }


def friendly_error(err) -> str:
    """把 GitHub / git 的常见拒绝原因翻译成可操作的提示。"""
    text = str(err)
    low = text.lower()
    if "without `workflow` scope" in low or "workflow" in low and "scope" in low:
        return (
            "推送被 GitHub 拒绝：令牌缺少修改 workflow 的权限。\n"
            "传统令牌：需勾选 workflow（以及 repo）；\n"
            "细粒度令牌：需授予 Workflows = Read and write。\n"
            f"原始信息：{text[:300]}"
        )
    if "repository not found" in low or "not found" in low and "git" in low:
        return (
            "仓库不可见或不存在：确认仓库名正确，且令牌已授权该仓库。\n"
            "细粒度令牌：需在 Repository access 中勾选该仓库。\n"
            f"原始信息：{text[:200]}"
        )
    if "authentication failed" in low or "invalid username or password" in low or "bad credentials" in low:
        return f"认证失败：令牌无效、过期或已撤销。请到「本地配置」重新添加令牌。原始信息：{text[:200]}"
    if "protected branch" in low or "gh006" in low:
        return f"目标分支受保护，无法直接推送。CodeVoyage 会推送到新分支再建 PR，请检查仓库分支保护规则。原始信息：{text[:200]}"
    if "401" in text:
        return f"令牌无效或已过期（401）。{TOKEN_HINTS['classic'][0]}；细粒度令牌请确认未过期。原始信息：{text[:200]}"
    if "404" in text or "Not Found" in text:
        return (
            "GitHub 返回 404：仓库不可见、路径不存在，或令牌未授权该仓库。\n"
            "细粒度令牌：确认已勾选目标仓库，并授予 Contents=Read and write。\n"
            "传统令牌：确认勾选了 repo（私有仓库）。\n"
            f"原始信息：{text[:200]}"
        )
    if "403" in text:
        return (
            "GitHub 拒绝了该操作（403），通常是令牌权限不足。\n"
            "细粒度令牌：需勾选目标仓库，并授予 Contents=Read and write、Pull requests=Read and write、"
            "Workflows=Read and write（改 .github/workflows 必需）。\n"
            "传统令牌：需勾选 repo；改 workflow 还需 workflow 权限。\n"
            f"原始信息：{text[:300]}"
        )
    return text[:300]


def is_retryable(err) -> bool:
    """该错误是否值得换下一个令牌重试（凭据/权限/可见性问题）。"""
    text = str(err).lower()
    return any(k in text for k in (
        "401", "403", "404", "unauthorized", "forbidden", "not found",
        "bad credentials", "resource not accessible",
    ))


def _status_of(err):
    status = getattr(err, "status", None)
    if status:
        return status
    text = str(err)
    for code in ("401", "403", "404"):
        if code in text:
            return int(code)
    return None


def list_workflows(token: str, repo_full: str) -> dict:
    """列出仓库 .github/workflows 下的 yml/yaml，标记哪个是 CodeVoyage 的、是否含作者校验。

    返回 {exists, files, reason, retryable}
      - 目录不存在（404）视为「未安装」，不算错误；
      - 仓库不可见 / 无权限时 retryable=True，调用方可换下一个令牌重试。
    """
    try:
        repo = gh(token).get_repo(repo_full)
    except Exception as e:
        return {"exists": False, "files": [], "reason": friendly_error(e), "retryable": is_retryable(e)}
    try:
        items = repo.get_contents(".github/workflows")
    except Exception as e:
        if _status_of(e) == 404:
            return {"exists": False, "files": [],
                    "reason": "仓库中还没有 .github/workflows 目录（视为未安装）", "retryable": False}
        return {"exists": False, "files": [], "reason": friendly_error(e), "retryable": is_retryable(e)}
    if not isinstance(items, list):
        items = [items]
    files = []
    for it in items:
        if getattr(it, "type", "") != "file" or not it.name.endswith((".yml", ".yaml")):
            continue
        try:
            content = it.decoded_content.decode("utf-8", "replace")
        except Exception:
            content = ""
        files.append({
            "name": it.name,
            "path": it.path,
            "is_codevoyage": ("CodeVoyage Issue Report" in content) or ("BACKEND_WEBHOOK_URL" in content),
            "has_author_check": "comment_author" in content,
        })
    return {"exists": bool(files), "files": files, "reason": "", "retryable": False}


def commit_workflow_pr(token: str, repo_full: str, path: str, content: str,
                       title: str = "chore: add CodeVoyage workflow",
                       author_name: str | None = None,
                       author_email: str | None = None) -> dict:
    """按「准备特性分支 → 提交代码 → 推送远端 → 调接口创建 PR」四步安装 workflow。

    author_name/author_email 为提交身份（默认 3890320020@qq.com）。
    返回 {pr_url, branch, base, existed, steps}；失败抛 RuntimeError（含指引）。
    本地临时目录在结束时清理。
    """
    import os
    import tempfile

    from tools import GitRepo  # 延迟导入，避免无关场景加载 git 工具

    name = author_name or "CodeVoyage AI"
    email = author_email or "3890320020@qq.com"
    branch = f"codevoyage/workflow-{int(time.time())}"
    workdir = tempfile.mkdtemp(prefix="codevoyage-wf-")
    repo_dir = os.path.join(workdir, "repo")
    steps: list[str] = []

    def _log(msg: str) -> None:
        steps.append(msg)
        try:
            from centre import paths  # 延迟导入，写入运行日志

            paths.append_log(f"[workflow安装] {msg}")
        except Exception:
            pass

    try:
        # 0) 读取仓库默认分支
        repo = gh(token).get_repo(repo_full)
        base = repo.default_branch
        existed = False

        # 1) 准备特性分支（浅克隆 → 建分支）
        GitRepo.clone_shallow(repo_full, token, repo_dir)
        GitRepo.set_identity(repo_dir, name, email)
        GitRepo.create_branch(repo_dir, branch)
        _log(f"1/4 特性分支已就绪：{branch}（基于 {base}）")

        # 2) 提交代码
        existed = os.path.isfile(os.path.join(repo_dir, path.replace("/", os.sep)))
        GitRepo.write_file(repo_dir, path, content)
        commit_hash = GitRepo.commit_paths(repo_dir, [path], f"{title}\n\n提交邮箱：{email}")
        _log(f"2/4 已提交 {path}（commit {commit_hash}，作者 {email}）")

        # 3) 推送远端（浅克隆被拒时补全历史后重试一次）
        try:
            GitRepo.push(repo_dir, repo_full, token, branch)
        except RuntimeError as push_err:
            if "shallow" in str(push_err).lower():
                _log("浅克隆推送被拒，补全历史后重试")
                GitRepo.unshallow(repo_dir)
                GitRepo.push(repo_dir, repo_full, token, branch)
            else:
                raise
        _log(f"3/4 已推送到远端分支 {branch}")

        # 4) 调接口创建 PR
        body = (
            "由 CodeVoyage 本地控制台自动提交。\n\n"
            f"- 文件：`{path}`\n"
            f"- 分支：`{branch}` → `{base}`\n"
            f"- 提交邮箱：`{email}`\n"
            "- **合并本 PR 后** workflow 才会生效\n"
            "- 合并前请在仓库 Settings → Secrets and variables → Actions 中配置：\n"
            "  - `BACKEND_WEBHOOK_URL`\n"
            "  - `BACKEND_WEBHOOK_SECRET`\n"
        )
        pr = repo.create_pull(title=title, body=body, head=branch, base=base)
        _log(f"4/4 已创建 PR：{pr.html_url}")
        return {"pr_url": pr.html_url, "branch": branch, "base": base,
                "existed": existed, "steps": steps}
    except Exception as e:
        suffix = f"（已完成：{'；'.join(steps) if steps else '无'}）"
        raise RuntimeError(f"{friendly_error(e)}{suffix}") from e
    finally:
        GitRepo.remove_dir(workdir)
