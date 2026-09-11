"""通知工作流：任务结束后自动回帖（Issue / PR），补齐 PR 侧的通知能力（Issue #20）。

背景：现有流程只覆盖「自动处理 Issue → 提交 PR」，PR 建好后触发者与仓库关注者收不到
任何回执（失败时更是完全静默），只能在 GitHub 通知里凭事件自己推断。本模块把「通知」
做成工作流里的一步：任务结束时在任务来源处（Issue 或 PR）自动回帖。

覆盖场景：
1. Issue / Issue 评论触发：任务结束后在原 Issue 回帖 —— 成功附 PR 链接与结论摘要，
   失败 / 放弃附原因与重试指引；
2. PR（PR 提交 / 更新 / 关闭 / PR 评论）触发：任务结束后在该 PR 回帖，答复「已收到并
   已处理」，并附本次处理新建的 PR 链接；
3. CodeVoyage 新建的 PR 上也会自动回帖结论摘要，PR 关注者无需回到 Issue 找信息。

安全与边界（与项目既有安全策略一致）：
- 只评论，不合并 / 不关闭 / 不删除任何内容，也不修改 PR 分支；
- 评论内容先脱敏（本机路径、令牌样式 → 占位符）再发送，长度截断；
- 令牌走 paths.resolve_tokens（仓库专属 → 全局，顺序回退），失败只写日志；
- 同一条通知带固定隐藏标记，重复执行任务不会刷屏；
- 可用环境变量 CODEVOYAGE_DISABLE_PR_NOTIFY=1 整体关闭（默认开启）。
"""
import os
import re
import time

from centre import paths, proxy

# 隐藏标记：用于去重（同一条通知不重复发送），对读者不可见
MARKER_SOURCE = "<!-- CodeVoyage:notify:source -->"   # 任务来源处（Issue / 来源 PR）
MARKER_PR = "<!-- CodeVoyage:notify:pr -->"           # 本次新建的 PR

_MAX_BODY = 4000

# 评论内容脱敏：本机路径 / 令牌样式不进 GitHub
_SECRET_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
)
_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\[^\s\"'<>|,;)]+"),                # Windows 绝对路径
    re.compile(r"\\\\[^\s\"'<>|,;)]+"),                       # UNC 路径
    re.compile(r"/(?:home|Users|root|tmp)/[^\s\"'<>|,;)]+"),  # 常见 *nix 用户目录
)
_LOGIN_RE = re.compile(r"[^A-Za-z0-9-]+")


# ---------------------------------------------------------------- 开关与工具
def enabled() -> bool:
    """自动回帖开关（默认开启；CODEVOYAGE_DISABLE_PR_NOTIFY=1 关闭）。"""
    return str(os.getenv("CODEVOYAGE_DISABLE_PR_NOTIFY") or "").strip().lower() not in (
        "1", "true", "yes", "on",
    )


def _sanitize(text: str) -> str:
    """评论内容脱敏：去掉本机路径与令牌样式，避免通过 GitHub 泄露本机信息。"""
    out = str(text or "")
    if not out:
        return ""
    for known in (paths.BASE_DIR, paths.REPO_DIR):
        if known:
            out = out.replace(known, "<本地目录>")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***", out)
    for pat in _PATH_PATTERNS:
        out = pat.sub("<路径>", out)
    return out.strip()


def _clip(text: str, limit: int = _MAX_BODY) -> str:
    """评论正文截断（GitHub 评论长度有限，且通知不需要全文）。"""
    body = str(text or "").strip()
    if len(body) > limit:
        body = body[:limit].rstrip() + "…"
    return body


def _mention(task: dict) -> str:
    """@ 触发者（仅评论触发时拿得到作者），机器人账号不 @。"""
    author = _LOGIN_RE.sub("", str((task or {}).get("comment_author") or "")).strip("-")
    if not author or author.endswith("bot") or author == "github-actions":
        return ""
    return f"@{author} "


def _quote(text: str) -> str:
    """把 AI 结论转成引用块，保持可读。"""
    lines = [ln.rstrip() for ln in _sanitize(text).splitlines() if ln.strip()]
    return "\n".join("> " + ln for ln in lines)


def _foot() -> str:
    return ("_本条为 CodeVoyage 通知工作流自动回帖；如需继续调整，"
            "在 Issue / PR 下评论并带上触发关键词即可。_")


# ---------------------------------------------------------------- 正文拼装
def _success_source_body(task: dict, pr_url: str, conclusion: str) -> str:
    """任务成功：在来源 Issue / PR 上回帖。"""
    rows = [MARKER_SOURCE, ""]
    rows.append(f"{_mention(task)}本任务已由 CodeVoyage 处理完成 ✅")
    if pr_url:
        rows.append("")
        rows.append(f"- Pull Request：{pr_url}")
    rows.append(f"- 处理对象：`{task.get('repo_full', '')}#{task.get('issue_number', '')}`")
    if conclusion:
        rows += ["", "改动摘要（Conclusion）：", "", _quote(conclusion)]
    rows += ["", _foot()]
    return "\n".join(rows)


def _success_pr_body(task: dict, conclusion: str) -> str:
    """任务成功：在本次新建的 PR 上回帖（PR 关注者也能收到通知）。"""
    rows = [MARKER_PR, ""]
    rows.append("CodeVoyage 已提交本 PR，以下为本次改动的结论摘要：")
    rows += ["", _quote(conclusion) or "> _（AI 未给出结论摘要）_"]
    rows.append("")
    action = task.get("event_action") or ""
    rows.append(f"- 来源：`{task.get('repo_full', '')}#{task.get('issue_number', '')}`"
                + (f"（{action} 事件）" if action else ""))
    if task.get("issue_url"):
        rows.append(f"- Issue：{task['issue_url']}")
    rows.append("- 合并进默认分支后，CodeVoyage 工作分支会被后台巡检自动销毁")
    rows += ["", _foot()]
    return "\n".join(rows)


def _failed_body(task: dict, error: str) -> str:
    """任务未完成（失败 / 用户放弃）：同样回帖，避免关注者干等。"""
    rows = [MARKER_SOURCE, ""]
    rows.append(f"{_mention(task)}CodeVoyage 未能完成本任务 ⚠️")
    if error:
        rows += ["", "原因：", "", _quote(error)]
    rows += [
        "",
        "常见原因：令牌权限不足（需 Contents / Pull requests 读写）、令牌看不到该仓库、"
        "网络受限、本机没有可用令牌，或本机用户在确认弹窗里放弃了本次任务。",
        "处理后在原处评论并带上触发关键词即可重试；本机运行日志里有完整原文，便于排查。",
        "",
        _foot(),
    ]
    return "\n".join(rows)


# ---------------------------------------------------------------- 发送
def _post(repo_full: str, number, body: str, marker: str) -> dict:
    """按令牌顺序在某个 Issue / PR 上回帖，返回 {posted, skipped, url, reason}。"""
    out = {"posted": False, "skipped": False, "url": "", "reason": ""}
    if not number:
        out["reason"] = "缺少 Issue / PR 编号"
        return out
    candidates = paths.resolve_tokens(repo_full)
    if not candidates:
        out["reason"] = "该仓库没有可用令牌，无法回帖"
        paths.append_log(f"[通知] {repo_full}#{number} 跳过：{out['reason']}")
        return out
    try:
        from GithubTool import pulls       # 延迟导入：避免无令牌时也加载 PyGithub
    except Exception as e:
        out["reason"] = f"GitHub 组件不可用：{e}"
        return out

    last = ""
    for token, source in candidates:
        try:
            if pulls.has_marker(token, repo_full, number, marker):
                out.update({"skipped": True, "reason": "已通知过，跳过重复回帖"})
                return out
            res = proxy.github_call(pulls.comment, token, repo_full, number, _clip(body))
        except Exception as e:
            last = str(e)
            continue
        if res.get("ok"):
            out.update({"posted": True, "url": res.get("url", ""), "reason": ""})
            paths.append_log(
                f"[通知] 已在 {repo_full}#{number} 回帖：{res.get('url', '')}（令牌来源 {source}）"
            )
            return out
        last = res.get("reason") or last
    out["reason"] = last or "全部令牌均无法回帖"
    paths.append_log(f"[通知] {repo_full}#{number} 回帖失败：{out['reason']}")
    return out


def on_task_finished(task: dict, pr_url: str = "", ok: bool = True,
                     error: str = "", conclusion: str = "") -> dict:
    """任务结束后的自动回帖（尽力而为，绝不抛异常）。

    成功：来源 Issue / PR 回帖（附 PR 链接与结论）+ 新建 PR 回帖（附结论）；
    失败 / 放弃：来源 Issue / PR 回帖（附脱敏后的原因）。
    返回 {posted, urls, reason}；任何异常都只记日志，不影响任务结果。
    """
    result = {"posted": False, "urls": [], "reason": ""}
    try:
        if not enabled():
            result["reason"] = "已通过 CODEVOYAGE_DISABLE_PR_NOTIFY 关闭自动回帖"
            return result
        repo_full = str((task or {}).get("repo_full") or "").strip()
        if not repo_full:
            result["reason"] = "缺少仓库信息"
            return result

        targets = []
        number = (task or {}).get("issue_number")
        if ok:
            if number:
                targets.append((number, MARKER_SOURCE,
                                _success_source_body(task, pr_url, conclusion)))
            new_pr = _pr_number(pr_url)
            if new_pr and new_pr != number:
                targets.append((new_pr, MARKER_PR, _success_pr_body(task, conclusion)))
        elif number:
            targets.append((number, MARKER_SOURCE, _failed_body(task, error)))

        for target, marker, body in targets:
            res = _post(repo_full, target, body, marker)
            if res.get("posted"):
                result["urls"].append(res["url"])
            elif not res.get("skipped") and not result["reason"]:
                result["reason"] = res.get("reason", "")
            time.sleep(1)      # 轻微间隔：避免连续评论触发 GitHub 二级速率限制
        result["posted"] = bool(result["urls"])
    except Exception as e:     # 通知是辅助能力，绝不能带崩任务主流程
        result["reason"] = f"通知异常：{e}"
        paths.append_log(f"[通知] 自动回帖异常：{e}")
    return result


def _pr_number(pr_url: str) -> int:
    """从 PR 链接解析编号（复用 GithubTool.pulls 的解析规则，失败返回 0）。"""
    try:
        from GithubTool import pulls

        return pulls.pr_number(pr_url)
    except Exception:
        return 0
