"""Agent 服务主循环。

单线程消费中心队列：
1. 发现 waiting/confirming 任务 -> 置为 confirming 并等待用户确认（控制台按钮），
   超时（10s）默认执行；
2. 用户放弃 -> 本地标记 putout，并向远端回执 PutOut；
3. 执行（Agent.main.run_task：克隆/AI 修改/提交/推送/PR）：
   - 成功：本地 done + pr_url，远端回执 OK；
   - 失败：本地 failed + error，远端回执 Failed。
"""
import time

import Agent.main as runner
from centre import core, paths, remote


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
                time.sleep(1)
                continue

            uid = task["uuid"]
            core.touch_confirm(uid)
            current = core.find_by_uuid(uid) or task
            repo_full = current.get("repo_full", "")
            issue_no = current.get("issue_number", "")
            paths.append_log(f"等待确认：{repo_full}#{issue_no}（10s 内未操作自动执行）")

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
        except Exception as e:
            if task:
                uid = task["uuid"]
                core.finish(uid, "failed", error=str(e))
                paths.append_log(f"任务失败 {task.get('repo_full', '')}#{task.get('issue_number', '')}: {e}")
                _report(uid, "Failed", error=str(e))
            time.sleep(1)


if __name__ == "__main__":
    main()
