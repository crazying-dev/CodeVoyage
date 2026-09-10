"""本地任务管理（单进程内共享）。

职责：
- 接收 GetIssue 组件从远端拉到的 Issue，写入对应仓库的 wait/list.json；
- 维护任务状态机：waiting -> confirming -> running -> done | putout | failed；
- 供 Agent 引擎消费（确认、超时、结果落库）以及 Agent 可视化轮询。

目录：~/.CodeVoyage/Agent/repo/<repo_hash>/
    wait/list.json  任务列表
    Info.json       仓库元信息
    history.json    该仓库对话历史
    work/           实际 clone 的工作目录
"""
import os
import threading
import time
from datetime import datetime

import centre.paths as paths

_lock = threading.RLock()

VALID_STATES = ("waiting", "confirming", "running", "done", "putout", "failed")
CONFIRM_SECONDS = 10  # 未操作默认执行


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------ 基础读写 ------------------------------
def _read_tasks(repo_full: str) -> list:
    return paths.load_json(paths.wait_file(repo_full), [])


def _write_tasks(repo_full: str, tasks: list) -> None:
    os.makedirs(os.path.dirname(paths.wait_file(repo_full)), exist_ok=True)
    paths.save_json(paths.wait_file(repo_full), tasks)


def _iter_all_tasks():
    """遍历所有仓库目录下的任务（不保证顺序稳定）。"""
    if not os.path.isdir(paths.REPO_DIR):
        return
    for name in os.listdir(paths.REPO_DIR):
        folder = os.path.join(paths.REPO_DIR, name)
        wait_path = os.path.join(folder, "wait", "list.json")
        if not os.path.isfile(wait_path):
            continue
        repo_full = paths.load_json(os.path.join(folder, "Info.json"), {}).get("repo_full")
        if not repo_full:
            continue
        for task in paths.load_json(wait_path, []):
            task["repo_full"] = repo_full
            yield task


def find_by_uuid(uid: str) -> dict | None:
    with _lock:
        for task in _iter_all_tasks():
            if task.get("uuid") == uid:
                return task
        return None


def list_repo_dirs() -> list:
    out = []
    if not os.path.isdir(paths.REPO_DIR):
        return out
    for name in os.listdir(paths.REPO_DIR):
        folder = os.path.join(paths.REPO_DIR, name)
        if not os.path.isdir(folder):
            continue
        info = paths.load_json(os.path.join(folder, "Info.json"), {})
        out.append({"hash": name, "repo_full": info.get("repo_full", name), "repo_dir": folder})
    return out


# ------------------------------ 任务增改 ------------------------------
def ensure_repo_info(repo_full: str) -> None:
    rdir = paths.repo_dir(repo_full)
    os.makedirs(os.path.join(rdir, "wait"), exist_ok=True)
    os.makedirs(paths.work_dir(repo_full), exist_ok=True)
    info_path = paths.info_file(repo_full)
    if not os.path.exists(info_path):
        paths.save_json(info_path, {"repo_full": repo_full, "created_at": _now()})


def enqueue_issue(issue: dict) -> dict:
    """把一条来自远端的 Issue 写入队列。重复（存在同编号未完成任务）时返回已有任务。"""
    repo_full = issue.get("repo_full") or ""
    issue_number = issue.get("issue_number")
    if not repo_full:
        raise ValueError("repo_full missing")
    with _lock:
        ensure_repo_info(repo_full)
        tasks = _read_tasks(repo_full)
        for t in tasks:
            if t.get("issue_number") == issue_number and t.get("state") in ("waiting", "confirming", "running"):
                return t
        task = {
            "uuid": issue.get("uuid"),
            "repo_full": repo_full,
            "issue_url": issue.get("issue_url") or f"https://github.com/{repo_full}/issues/{issue_number}",
            "issue_number": issue_number,
            "title": issue.get("title") or "",
            "body": issue.get("body") or "",
            "body_text": issue.get("body_text") or issue.get("body") or "",
            "comments": issue.get("comments") or [],
            "trigger_word": issue.get("trigger_word") or "",
            "trigger_source": issue.get("trigger_source") or "",
            "state": "waiting",
            "received_at": _now(),
            "updated_at": _now(),
            "confirm_due": None,
            "decided": None,
            "pr_url": "",
            "error": "",
            "steps": [],
        }
        tasks.insert(0, task)
        tasks = tasks[:200]
        _write_tasks(repo_full, tasks)
        paths.append_log(f"收到任务 {repo_full}#{issue_number} -> {task['uuid'][:8]}")
        return task


def _replace(repo_full: str, uid: str, mutator) -> dict | None:
    tasks = _read_tasks(repo_full)
    for t in tasks:
        if t.get("uuid") == uid:
            mutator(t)
            t["updated_at"] = _now()
            _write_tasks(repo_full, tasks)
            return t
    return None


def touch_confirm(uid: str) -> dict | None:
    """把任务置为 confirming，并给 10 秒用户确认窗口。"""
    def _do(t):
        if t["state"] in ("waiting", "confirming"):
            t["state"] = "confirming"
            t["confirm_due"] = time.time() + CONFIRM_SECONDS
            t["decided"] = None
            t.setdefault("steps", []).append(f"{_now()} 等待用户确认（{CONFIRM_SECONDS}s 后自动执行）")
    return _replace(_repo_of(uid), uid, _do) if _repo_of(uid) else None


def decide(uid: str, yes: bool) -> bool:
    """控制台确认结果。返回是否找到任务。"""
    repo_full = _repo_of(uid)
    if not repo_full:
        return False

    def _do(t):
        t["decided"] = bool(yes)
        t.setdefault("steps", []).append(f"{_now()} 用户选择{'执行' if yes else '放弃'}")
    return _replace(repo_full, uid, _do) is not None


def wait_decision(uid: str, timeout: float = CONFIRM_SECONDS + 2) -> bool:
    """等待用户确认或超时。返回 True 执行 / False 放弃。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = find_by_uuid(uid)
        if task and task.get("decided") is not None:
            return bool(task["decided"])
        time.sleep(0.3)
    return True  # 超时默认执行


def next_unprocessed() -> dict | None:
    """取最早一条 waiting/confirming 且未超时未决断的任务。"""
    with _lock:
        candidates = []
        for task in _iter_all_tasks():
            if task.get("state") not in ("waiting", "confirming"):
                continue
            if task.get("decided") is False:  # 已决断放弃，等待引擎清理
                continue
            candidates.append(task)
        if not candidates:
            return None
        candidates.sort(key=lambda t: t.get("received_at", ""))
        return candidates[0]


def _repo_of(uid: str) -> str | None:
    task = find_by_uuid(uid)
    return task.get("repo_full") if task else None


def mark_running(uid: str) -> None:
    repo_full = _repo_of(uid)
    if not repo_full:
        return

    def _do(t):
        t["state"] = "running"
        t.setdefault("steps", []).append(f"{_now()} 开始执行")
    _replace(repo_full, uid, _do)
    set_activity({"running_uuid": uid, "status": "running", "started_at": _now()})


def finish(uid: str, state: str, pr_url: str = "", error: str = "") -> dict | None:
    repo_full = _repo_of(uid)
    if not repo_full or state not in VALID_STATES:
        return None

    def _do(t):
        t["state"] = state
        t["pr_url"] = pr_url
        t["error"] = error
        t["decided"] = True
        t.setdefault("steps", []).append(f"{_now()} 结束：{state}")
    task = _replace(repo_full, uid, _do)
    set_activity({"running_uuid": "", "status": "idle", "started_at": ""})
    return task


def tasks_snapshot(limit: int = 100) -> list:
    """Agent 可视化：汇总全部任务，最新在前。"""
    with _lock:
        items = list(_iter_all_tasks())
    items.sort(key=lambda t: t.get("received_at", ""), reverse=True)
    return items[:limit]


# ------------------------------ 全局运行状态 ------------------------------
def set_activity(patch: dict) -> None:
    with _lock:
        state = paths.load_json(paths.STATE_PATH, {})
        state.update(patch)
        state["updated_at"] = _now()
        paths.save_json(paths.STATE_PATH, state)


def get_state() -> dict:
    with _lock:
        state = paths.load_json(paths.STATE_PATH, {})
    state.setdefault("running_uuid", "")
    state.setdefault("status", "idle")
    state.setdefault("started_at", "")
    state["server_time"] = _now()
    return state
