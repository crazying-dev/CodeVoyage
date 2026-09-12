"""Agent 服务主循环。

单线程消费中心队列：
1. 发现 waiting/confirming 任务 -> 置为 confirming，右下角弹窗 + 控制台按钮等待用户确认，
   超时（10s）默认执行；
2. 用户放弃 -> 本地标记 putout，自动回复 Issue（通知工作流，Issue #20），并向远端回执 PutOut；
3. 执行（Agent.main.run_task：克隆/AI 修改/提交/推送/PR）：
   - 成功：本地 done + pr_url，自动回复 Issue / PR（通知工作流），远端回执 OK；
   - 失败：本地 failed + error，自动回复 Issue 说明失败原因（脱敏后），远端回执 Failed。

安全（审计整改）：回执给远端的 error 文本先做脱敏（`_sanitize_error`）——去掉本机
绝对路径、用户目录、令牌样式与带凭据的 URL。原始异常只写入本机日志供排查，不会
离开本机（避免把内部路径 / 令牌片段通过远端接口泄露出去）。同一份脱敏实现（
centre.sanitize）也用于自动回复到 GitHub 的通知正文。
"""
import time

import Agent.main as runner
from centre import branch_gc, core, notify, paths, remote

# 权限类失败时追加的排查提示（自动回复通知需要对 Issue 有写权限）
_PERMISSION_HINT = (
    "\n提示：请检查 GitHub Token 权限。"
    "传统令牌需勾选 repo（改 workflow 还需 workflow）；"
    "细粒度令牌需勾选该仓库并授予 Contents / Pull requests / Workflows 读写，"
    "以及在 Issue 下自动回复通知所需的 Issues 读写。"
)


def _sanitize_error(err, limit: int = 800) -> str:
    """错误信息脱敏：本机路径 / 凭据 → 占位符，并截断长度。

    统一实现见 centre.sanitize（回执远端与自动回复通知共用同一套规则）。
    """
    from centre import sanitize

    return sanitize.sanitize(err, limit)


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


def _notify(task: dict, kind: str, error: str = "", pr_url: str = "") -> None:
    """通知工作流（Issue #20）：自动回复 Issue，说明任务结果。

    成功路径由 Agent.main 在拿到 PR 链接后发送（那里才有结论与 PR 状态）；
    这里覆盖「失败 / 用户放弃」两种结果，保证任何结局提交者都能收到反馈。
    只写评论：不改代码、不改分支、不改 Issue 状态；失败只记日志。
    """
    try:
        from centre import pr_notify

        result = pr_notify.notify(task or {}, kind=kind, error=error, pr_url=pr_url)
        text = pr_notify.format_result(result)
    except Exception as e:  # 通知绝不能把 Agent 主循环带崩
        paths.append_log(f"通知发送异常（{kind}）：{e}")
        return
    first = next((line for line in text.splitlines() if line.strip()), "")
    paths.append_log(f"通知（{kind}）：{first}")


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
                # 通知工作流：放弃也要给提交者一个反馈（未改动任何内容）
                _notify(current, "putout")
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
                    err_text += _PERMISSION_HINT
                core.finish(uid, "failed", error=err_text)
                # 本机日志保留原文（不出本机），便于排查
                paths.append_log(
                    f"任务失败 {task.get('repo_full', '')}#{task.get('issue_number', '')}: {raw}"
                )
                # 通知工作流：失败原因（脱敏后）写进 Issue 回复，避免提交者干等
                _notify(core.find_by_uuid(uid) or task, "failed", error=err_text)
                _report(uid, "Failed", error=err_text)
            time.sleep(1)


if __name__ == "__main__":
    main()
