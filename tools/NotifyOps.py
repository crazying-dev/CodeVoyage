"""通知工具（由 AI / 程序调用，Issue #20）。

notify_issue：在 Issue（或 PR）下回复一条 Markdown 通知，用于把处理进度或结论同步给
Issue 提交者 —— 任务结束时的自动回复由程序负责（centre.pr_notify），这里供 AI 在
处理过程中主动补充说明（例如需要人工确认、按约束拒绝某类改动）。

边界：
- 只用「留言」这一种写动作：不改代码、不改分支、不改 Issue 状态（不关闭 / 不锁定 / 不加标签）；
- 正文先脱敏（centre.sanitize），本机路径与令牌样式会被替换成占位符，所以不要把令牌、
  密钥或本机绝对路径写进正文；
- 令牌走本仓库（仓库专属 → 全局）顺序回退；发送失败返回以「错误：」开头的文本，
  不影响任务本身的结果；
- 发送成功后同样登记到任务（`core.note_notification`），控制台任务列表里能看到
  「已回复通知」，与程序自动回复的记录一致。
"""
from centre import pr_notify, sanitize
from tools import RepoOps


def _repo(repo: str) -> str:
    """仓库取值：显式传入优先，其次当前任务上下文。"""
    text = str(repo or "").strip()
    if text:
        return text
    return (RepoOps.state().get("repo_full") or "").strip()


def _remember(uid: str, sent: list) -> None:
    """把 AI 主动发送的通知登记到任务步骤（控制台可见）；失败不影响结果。"""
    uid = str(uid or "").strip()
    if not uid or not sent:
        return
    try:
        from centre import core

        name, url = sent[0]
        core.note_notification(uid, "manual", target=name, url=url, text="已回复通知（AI 主动）")
    except Exception:
        pass


def notify_issue(message: str, issue: str = "", pr: str = "", repo: str = "") -> str:
    """在 Issue（或 PR）下回复通知。

    message 必填：通知正文（Markdown，发送前自动脱敏、超长截断）
    issue   可选：Issue 编号或链接；留空表示当前任务的 Issue
    pr      可选：PR 编号或链接；填了会额外在 PR 下留一条同样的通知
    repo    可选：仓库 owner/name；留空表示当前任务仓库
    """
    repo_full = _repo(repo)
    if not repo_full:
        return "错误：未指定仓库，且当前任务没有仓库上下文（请传入 repo=owner/name）"
    body = sanitize.sanitize(str(message or "").strip(), 4000)
    if not body:
        return "错误：通知内容为空"

    state = RepoOps.state()
    target_issue = str(issue or "").strip() or str(state.get("issue_number") or "").strip()
    target_pr = str(pr or "").strip()
    if not target_issue and not target_pr:
        return "错误：没有可回复的对象（当前任务没有 Issue 编号，也没有传入 pr）"

    lines = [f"仓库 {repo_full} 通知："]
    errors = []
    sent = []
    for name, ref in (("PR", target_pr), ("Issue", target_issue)):
        if not ref:
            continue
        res = pr_notify.reply(repo_full, ref, body)
        if res.get("ok"):
            sent.append((name, res.get("url") or ""))
            lines.append(f"  - {name} {ref}：已回复 {res.get('url') or ''}".rstrip())
        else:
            errors.append(f"{name} {ref}：{res.get('reason') or '回复失败'}")
            lines.append(f"  - {name} {ref}：失败（{res.get('reason') or '未知原因'}）")

    if not sent:
        return "错误：通知发送失败：" + "；".join(errors)
    _remember(state.get("uid"), sent)
    if errors:
        lines.append("说明：部分目标发送失败，可稍后重试；通知失败不影响本次任务结果。")
    return "\n".join(lines)
