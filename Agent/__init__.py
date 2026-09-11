"""Agent 服务主循环。

单线程消费中心队列：
1. 发现 waiting/confirming 任务 -> 置为 confirming，右下角弹窗 + 控制台按钮等待用户确认，
   超时（10s）默认执行；
2. 用户放弃 -> 本地标记 putout，向远端回执 PutOut，并在来源 Issue / PR 上自动回帖告知
   （通知工作流，Issue #20）；
3. 执行（Agent.main.run_task：克隆/AI 修改/提交/推送/PR/通知）：
   - 成功：本地 done + pr_url，远端回执 OK，并在来源 Issue / PR 与新建 PR 上自动回帖；
   - 失败：本地 failed + error，远端回执 Failed，并在来源 Issue / PR 上自动回帖说明原因。

安全（审计整改）：回执给远端的 error 文本先做脱敏（`_sanitize_error`）——去掉本机
绝对路径、用户目录、令牌样式与带凭据的 URL。原始异常只写入本机日志供排查，不会
离开本机（避免把内部路径 / 令牌片段通过远端接口泄露出去）。回帖内容在 centre.pr_notify
里同样先脱敏，再发往 GitHub。
"""
import re
import time

import Agent.main as runner
from centre import branch_gc, core, notify, paths, pr_notify, remote

# 需要脱敏的凭据样式
_SECRET_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*\S+"),
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),          # URL 内嵌凭据
)

# 需要脱敏的本机路径样式
_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\[^\s\"'<>|,;)]+"),              # Windows 绝对路径
    re.compile(r"\\\\[^\s\"'<>|,;)]+"),                     # UNC 路径
    re.compile(r"/(?:home|Users|root|tmp)/[^\s\"'<>|,;)]+"),  # 常见 *nix 用户目录
)


def _sanitize_error(err, limit: int = 800) -> str:
    """错误信息脱敏：本机路径 / 凭据 → 占位符，并截断长度。用于回执远端与本地展示。"""
    text = str(err or "")
    if not text:
        return ""
    for known in (paths.BASE_DIR, paths.REPO_DIR, paths.HOME_DIR):
        if known:
            text = text.replace(known, "<本地目录>")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) + "***") if m.groups() else "***", text)
    for pat in _PATH_PATTERNS:
        text = pat.sub("<路径>", text)
    text = text.strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _report(uuid: str, status: str, pr_url: str = "", error: str = "") -> None:
    """向远端回执执行结果（失败不影响本地主流程）；error 一律先脱敏。"""
    try:
        remote.call(
            "/api/Issue/Result",
            {"uuid": uuid, "status": status, "pr_url": pr_url, "error": _sanitize_error(error)},
            timeout=30,
        )
    except Exception as e:
        paths.append_log(f"回执远端失败 {uuid} {status}: {e}")


def main():
    paths.ensure_dirs()
    paths.append_log("Agent 服务已启动")
    while True:
        task = None
        try:
            task = core.next_unprocessed()
            if not task:
                # 空闲时巡检（内部已节流，默认 10 分钟一次）：
                # PR 合并进主分支后自动销毁对应工作分支，保证仓库分支管理干净
                branch_gc.sweep_due()
                time.sleep(1)
                continue

            uid = task["uuid"]
            core.touch_confirm(uid)
            current = core.find_by_uuid(uid) or task
            repo_full = current.get("repo_full", "")
            issue_no = current.get("issue_number", "")
            paths.append_log(f"等待确认：{repo_full}#{issue_no}（10s 内未操作自动执行）")
            # 右下角弹窗，可直接在窗口里确认；不操作则倒计时结束自动执行
            notify.ask(uid, repo_full, issue_no, current.get("title", ""), core.CONFIRM_SECONDS)

            agreed = core.wait_decision(uid, timeout=12)
            if not agreed:
                core.finish(uid, "putout", error="用户放弃")
                paths.append_log(f"用户放弃：{repo_full}#{issue_no}")
                # 通知工作流（Issue #20）：放弃执行也要回帖告知，避免 Issue / PR 侧无人知晓
                pr_notify.on_task_finished(
                    current, ok=False, error="用户在本机控制台放弃了本次任务。")
                _report(uid, "PutOut")
                continue

            core.mark_running(uid)
            paths.append_log(f"开始执行：{repo_full}#{issue_no}")
            result = runner.run_task(current)
            core.finish(uid, "done", pr_url=result.get("pr_url", ""))
            paths.append_log(f"任务完成：{repo_full}#{issue_no} PR={result.get('pr_url', '')}")
            _report(uid, "OK", pr_url=result.get("pr_url", ""))
            # 任务结束后立刻巡检一次：若用户已经合并 PR，工作分支随即被销毁
            branch_gc.sweep_due(force=True)
        except Exception as e:
            if task:
                uid = task["uuid"]
                raw = str(e)
                err_text = _sanitize_error(raw)
                if any(k in raw for k in ("Permission", "permission", "403", "denied", "Authentication")):
                    err_text += (
                        "\n提示：请检查 GitHub Token 权限。"
                        "传统令牌需勾选 repo（改 workflow 还需 workflow）；"
                        "细粒度令牌需勾选该仓库并授予 Contents / Pull requests / Workflows 读写。"
                    )
                core.finish(uid, "failed", error=err_text)
                # 本机日志保留原文（不出本机），便于排查
                paths.append_log(
                    f"任务失败 {task.get('repo_full', '')}#{task.get('issue_number', '')}: {raw}"
                )
                _report(uid, "Failed", error=err_text)
            time.sleep(1)


if __name__ == "__main__":
    main()
