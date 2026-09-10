"""GitHub API 封装（PAT 由本地配置注入，绝不硬编码/上传）。"""
import time

from github import Github
from github.Auth import Token


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


def list_workflows(token: str, repo_full: str) -> dict:
    """列出仓库 .github/workflows 下的 yml/yaml，标记哪个是 CodeVoyage 的、是否含作者校验。

    返回 {exists: bool, files: [{name, path, is_codevoyage, has_author_check}], reason: str}
    """
    repo = gh(token).get_repo(repo_full)
    try:
        items = repo.get_contents(".github/workflows")
    except Exception as e:  # 目录不存在或无权限
        return {"exists": False, "files": [], "reason": str(e)}
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
    return {"exists": bool(files), "files": files}


def commit_workflow_pr(token: str, repo_full: str, path: str, content: str,
                       title: str = "chore: add CodeVoyage workflow") -> dict:
    """新建分支提交 workflow 文件并发起 PR。

    返回 {pr_url, branch, base, existed}；existed=True 表示默认分支上已有同名文件（本次为更新）。
    """
    repo = gh(token).get_repo(repo_full)
    base = repo.default_branch
    branch = f"codevoyage/workflow-{int(time.time())}"
    base_sha = repo.get_branch(base).commit.sha
    repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)

    existed = False
    try:
        existing = repo.get_contents(path, ref=base)
        if isinstance(existing, list):
            existing = existing[0]
        existed = True
        repo.update_file(path, title, content, existing.sha, branch=branch)
    except Exception:
        repo.create_file(path, title, content, branch=branch)

    body = (
        "由 CodeVoyage 本地控制台自动生成并提交。\n\n"
        f"- 文件：`{path}`\n"
        "- **合并本 PR 后** workflow 才会生效\n"
        "- 合并前请在仓库 Settings → Secrets and variables → Actions 中配置：\n"
        "  - `BACKEND_WEBHOOK_URL`\n"
        "  - `BACKEND_WEBHOOK_SECRET`\n"
    )
    pr = repo.create_pull(title=title, body=body, head=branch, base=base)
    return {"pr_url": pr.html_url, "branch": branch, "base": base, "existed": existed}
