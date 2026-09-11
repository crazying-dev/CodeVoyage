"""Agent 服务主循环。

单线程消费中心队列：
1. 发现 waiting/confirming 任务 -> 置为 confirming，右下角弹窗 + 控制台按钮等待用户确认，
   超时（10s）默认执行；
2. 用户放弃 -> 本地标记 putout，并向远端回执 PutOut；
3. 执行（Agent.main.run_task：克隆/AI 修改/提交/推送/PR）：
   - 成功：本地 done + pr_url，远端回执 OK；
   - 失败：本地 failed + error，远端回执 Failed。
4. 空闲时巡检工作分支：PR 已合并进默认分支的 codevoyage/* 分支自动销毁（Issue #13）。
"""
import time

import Agent.main as runner
from centre import branch_gc, core, notify, paths, remote


def _report(uuid: str, status: str, pr_url: str = "", error: str = "") -> None:
    """向远端回执执行结果（失败不影响本地主流程）。"""
    try:
        remote.call(
            "/api/Issue/Result",
            {"uuid": uuid, "status": status, "pr_url": pr_url, "error": error},
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
                err_text = str(e)
                if any(k in err_text for k in ("Permission", "permission", "403", "denied", "Authentication")):
                    err_text += (
                        "\n提示：请检查 GitHub Token 权限。"
                        "传统令牌需勾选 repo（改 workflow 还需 workflow）；"
                        "细粒度令牌需勾选该仓库并授予 Contents / Pull requests / Workflows 读写。"
                    )
                core.finish(uid, "failed", error=err_text)
                paths.append_log(f"任务失败 {task.get('repo_full', '')}#{task.get('issue_number', '')}: {err_text}")
                _report(uid, "Failed", error=err_text)
            time.sleep(1)


if __name__ == "__main__":
    main()
