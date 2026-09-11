"""任务执行轨迹（AI 思考 / 工具调用 / 回答）的读写。

落盘位置：~/.CodeVoyage/Agent/repo/<repo_hash>/traces/<uuid>.json
控制台按 uuid 读取，即可回看某个任务的完整过程；执行中可轮询实时查看。

安全：uuid 来自远端，会直接拼进文件名，必须经 paths.safe_name 清洗，
否则 `../` 之类的输入可以写到数据目录之外（路径逃逸）。
"""
import os
import threading
from datetime import datetime

from centre import paths

_lock = threading.RLock()
MAX_EVENTS = 500


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dir(repo_full: str, create: bool = True) -> str:
    d = os.path.join(paths.repo_dir(repo_full), "traces")
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def _safe_uid(uid) -> str:
    return paths.safe_name(uid, "unknown")


def path_of(repo_full: str, uid: str) -> str:
    return os.path.join(_dir(repo_full), f"{_safe_uid(uid)}.json")


def start(repo_full: str, task: dict) -> dict:
    """新建一条轨迹（任务开始）。"""
    data = {
        "uuid": task.get("uuid", ""),
        "repo_full": repo_full,
        "issue_number": task.get("issue_number"),
        "issue_url": task.get("issue_url", ""),
        "title": task.get("title", ""),
        "status": "running",
        "started_at": _now(),
        "finished_at": "",
        "error": "",
        "events": [],
    }
    with _lock:
        paths.save_json(path_of(repo_full, data["uuid"]), data)
    return data


def append(repo_full: str, uid: str, event: dict) -> None:
    """追加一条事件（thinking / tool_call / tool_result / answer / error / note）。"""
    with _lock:
        data = load(repo_full, uid) or {}
        if not data:
            return
        event = dict(event or {})
        event["at"] = _now()
        events = list(data.get("events") or [])
        events.append(event)
        data["events"] = events[-MAX_EVENTS:]
        data["updated_at"] = event["at"]
        try:
            paths.save_json(path_of(repo_full, uid), data)
        except Exception:
            pass


def finish(repo_full: str, uid: str, status: str, error: str = "") -> None:
    with _lock:
        data = load(repo_full, uid) or {}
        if not data:
            return
        data["status"] = status
        data["error"] = error
        data["finished_at"] = _now()
        try:
            paths.save_json(path_of(repo_full, uid), data)
        except Exception:
            pass


def load(repo_full: str, uid: str) -> dict | None:
    if not repo_full or not uid:
        return None
    return paths.load_json(path_of(repo_full, uid), None)


def list_traces(repo_full: str, limit: int = 50) -> list:
    """该仓库的历史轨迹（仅摘要，不含事件明细）。"""
    d = _dir(repo_full, create=False)
    if not os.path.isdir(d):
        return []
    items = []
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        data = paths.load_json(os.path.join(d, name), None)
        if not isinstance(data, dict):
            continue
        items.append({
            "uuid": data.get("uuid", ""),
            "issue_number": data.get("issue_number"),
            "title": data.get("title", ""),
            "status": data.get("status", ""),
            "started_at": data.get("started_at", ""),
            "finished_at": data.get("finished_at", ""),
            "event_count": len(data.get("events") or []),
        })
    items.sort(key=lambda x: x.get("started_at", ""), reverse=True)
    return items[:limit]
