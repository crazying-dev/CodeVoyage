"""AI 对话（多会话）。

存储：~/.CodeVoyage/chat/
    <会话ID>.json        消息历史 {id, name, type, repo_full, created_at, updated_at, messages}
    <会话ID>.meta.json   会话配置 {id, name, type, repo_full, created_at, updated_at, message_count}

会话 ID 规则：
    仓库对话   sha1("repo:" + 仓库名) 取前 16 位十六进制 —— 同一仓库恒定，天然去重；
    我的对话   uuid4().hex。

每次提问都必须带会话 ID（仓库对话的 ID 由 /api/chat/for-repo 解析得到）。
配置文件随会话一起生成，列出会话时只读 *.meta.json，无需加载消息。

调用模型：复用服务端存储的 LLM 配置（多条并存，按顺序回退），
与 Agent 执行任务时用的是同一套配置。
"""
import hashlib
import os
import threading
import uuid
from datetime import datetime

from centre import paths

MAX_MESSAGES = 400          # 单个会话保留的最大消息数
TITLE_LEN = 24              # 会话名称取首条提问的前若干字
REPO_ID_LEN = 16            # 仓库名哈希取前若干位十六进制

_lock = threading.RLock()

TYPE_REPO = "repo"
TYPE_USER = "user"

SYSTEM_PROMPT = (
    "你是 CodeVoyage 的 AI 助手，服务于使用本工具处理 GitHub Issue 的开发者。\n"
    "回答要求：\n"
    "1. 用简体中文，直接给结论，避免客套；\n"
    "2. 使用 Markdown 排版（标题、列表、表格、代码块），代码块标注语言；\n"
    "3. 涉及命令与代码时给出可直接复制的内容；不确定的地方明确说明。"
)


class ChatError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dir() -> str:
    d = os.path.join(paths.BASE_DIR, "chat")
    os.makedirs(d, exist_ok=True)
    return d


def _clean_id(cid) -> str:
    """会话 ID：只允许十六进制，避免路径逃逸。"""
    text = str(cid or "").strip().lower()
    return text if text and all(c in "0123456789abcdef" for c in text) else ""


def _msg_path(cid: str) -> str:
    return os.path.join(_dir(), f"{cid}.json")


def _meta_path(cid: str) -> str:
    return os.path.join(_dir(), f"{cid}.meta.json")


def _repo_id(repo_full: str) -> str:
    """仓库对话 ID：仓库名哈希，同一仓库永远得到同一个 ID。"""
    raw = ("repo:" + (repo_full or "").strip()).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:REPO_ID_LEN]


def _iter_ids() -> set:
    """扫描目录里出现过的所有会话 ID（含只有配置或只有消息的旧数据）。"""
    ids = set()
    for name in os.listdir(_dir()):
        if name.endswith(".meta.json"):
            cid = _clean_id(name[: -len(".meta.json")])
        elif name.endswith(".json"):
            cid = _clean_id(name[: -len(".json")])
        else:
            continue
        if cid:
            ids.add(cid)
    return ids


# ---------------------------------------------------------------- 读写
def _load_meta(cid: str) -> dict | None:
    cid = _clean_id(cid)
    if not cid:
        return None
    data = paths.load_json(_meta_path(cid), None)
    return data if isinstance(data, dict) and data.get("id") else None


def _save_meta(meta: dict) -> None:
    paths.save_json(_meta_path(meta["id"]), meta)


def _load(cid: str) -> dict | None:
    cid = _clean_id(cid)
    if not cid:
        return None
    data = paths.load_json(_msg_path(cid), None)
    return data if isinstance(data, dict) and data.get("id") else None


def _save(conv: dict) -> None:
    """同时落盘消息与配置（配置文件是会话的权威索引）。"""
    conv["updated_at"] = _now()
    conv["name"] = (conv.get("name") or "").strip() or "新对话"
    if conv.get("repo_full"):
        conv["type"] = TYPE_REPO
    else:
        conv["type"] = conv.get("type") or TYPE_USER
    paths.save_json(_msg_path(conv["id"]), conv)
    _save_meta({
        "id": conv["id"],
        "name": conv["name"],
        "type": conv["type"],
        "repo_full": conv.get("repo_full") or "",
        "created_at": conv.get("created_at", ""),
        "updated_at": conv["updated_at"],
        "message_count": len(conv.get("messages") or []),
    })


def _ensure_meta(cid: str) -> dict | None:
    """取配置；旧版没有配置文件的会话在此补建（迁移 title -> name）。"""
    meta = _load_meta(cid)
    if meta:
        return meta
    conv = _load(cid)
    if not conv:
        return None
    if "name" not in conv:
        conv["name"] = conv.pop("title", "") or "新对话"
    conv.setdefault("type", TYPE_REPO if conv.get("repo_full") else TYPE_USER)
    with _lock:
        _save(conv)
    return _load_meta(cid)


def _rekey(old_id: str, new_id: str) -> dict:
    """把会话迁移到新的 ID（用于旧数据的仓库会话归并到哈希 ID）。"""
    conv = _load(old_id)
    if not conv:
        raise ChatError("会话不存在")
    with _lock:
        conv["id"] = new_id
        _save(conv)
        for p in (_msg_path(old_id), _meta_path(old_id)):
            try:
                os.remove(p)
            except OSError:
                pass
    return conv


def _find_by_repo(repo_full: str) -> dict | None:
    """找同仓库但 ID 不是哈希值的旧会话，用于迁移。"""
    for cid in _iter_ids():
        meta = _ensure_meta(cid)
        if meta and meta.get("type") == TYPE_REPO and meta.get("repo_full") == repo_full:
            return _load(cid)
    return None


# ---------------------------------------------------------------- 会话管理
def list_conversations() -> list:
    """会话摘要列表（按更新时间倒序）。"""
    out = []
    for cid in _iter_ids():
        meta = _ensure_meta(cid)
        if not meta:
            continue
        # 旧数据的仓库会话就地归并到哈希 ID，避免同仓库出现两条
        if meta.get("type") == TYPE_REPO:
            want = _repo_id(meta.get("repo_full") or "")
            if want and want != meta["id"] and not _load(want):
                _rekey(meta["id"], want)
                meta = _load_meta(want) or meta
        out.append({
            "id": meta["id"],
            "name": meta.get("name") or "新对话",
            "type": meta.get("type") or TYPE_USER,
            "repo_full": meta.get("repo_full") or "",
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "count": meta.get("message_count", 0),
        })
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return out


def create(name: str = "", cid: str = "", repo_full: str = "") -> dict:
    """新建「我的对话」；仓库对话请用 get_by_repo。"""
    with _lock:
        conv = {
            "id": _clean_id(cid) or uuid.uuid4().hex,
            "name": (name or "").strip() or "新对话",
            "type": TYPE_REPO if repo_full else TYPE_USER,
            "repo_full": (repo_full or "").strip(),
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
        }
        _save(conv)
        return conv


def get_by_repo(repo_full: str, create_if_missing: bool = True) -> dict | None:
    """取某仓库的会话：ID 由仓库名哈希得到，同一仓库只有一条。"""
    repo_full = (repo_full or "").strip()
    if not repo_full:
        return None
    cid = _repo_id(repo_full)
    conv = _load(cid)
    if conv:
        return conv
    old = _find_by_repo(repo_full)
    if old:
        return _rekey(old["id"], cid)
    if not create_if_missing:
        return None
    return create(name=repo_full, cid=cid, repo_full=repo_full)


def get(cid) -> dict | None:
    return _load(cid)


def remove(cid) -> bool:
    cid = _clean_id(cid)
    if not cid:
        return False
    removed = False
    for p in (_msg_path(cid), _meta_path(cid)):
        try:
            os.remove(p)
            removed = True
        except OSError:
            pass
    return removed


def rename(cid, name: str) -> dict | None:
    conv = _load(cid)
    if not conv:
        return None
    with _lock:
        conv["name"] = (name or "").strip() or conv.get("name") or "新对话"
        _save(conv)
    return conv


def append(cid, role: str, content: str) -> dict | None:
    conv = _load(cid)
    if not conv or role not in ("user", "assistant"):
        return None
    with _lock:
        conv.setdefault("messages", []).append({"role": role, "content": content, "at": _now()})
        conv["messages"] = conv["messages"][-MAX_MESSAGES:]
        if role == "user" and (conv.get("name") in ("", "新对话")):
            conv["name"] = content.strip().replace("\n", " ")[:TITLE_LEN] or "新对话"
        _save(conv)
    return conv


# ---------------------------------------------------------------- 调用模型
def llm_candidates() -> list:
    """与 Agent 共用同一套 LLM 配置（多条并存，顺序即回退顺序）。"""
    try:
        from Agent.main import _llm_candidates
    except Exception as e:
        raise ChatError(f"LLM 组件不可用：{e}")
    return _llm_candidates()


def complete(messages: list) -> tuple[str, str]:
    """按顺序尝试 LLM 配置，返回 (回复内容, 实际使用的配置名)。"""
    from openai import OpenAI

    candidates = llm_candidates()
    if not candidates:
        raise ChatError("未配置 LLM API Key：请到「配置」页添加")
    errors = []
    for cfg in candidates:
        try:
            client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
            resp = client.chat.completions.create(model=cfg["model"], messages=messages, stream=False)
            content = (resp.choices[0].message.content or "").strip()
            if content:
                return content, cfg["name"]
            errors.append(f"{cfg['name']}：返回内容为空")
        except Exception as e:
            errors.append(f"{cfg['name']}：{e}")
    raise ChatError("全部 LLM 配置调用失败：" + "；".join(errors))


def ask(cid, text: str) -> dict:
    """向指定会话（必须给 ID）提问并落库，返回 {conversation, reply, model}。"""
    text = (text or "").strip()
    if not text:
        raise ChatError("请输入内容")
    cid = _clean_id(cid)
    if not cid:
        raise ChatError("缺少对话 ID")
    conv = _load(cid)
    if not conv:
        raise ChatError("会话不存在，请重新打开该对话")
    append(cid, "user", text)

    conv = get(cid) or conv
    system = SYSTEM_PROMPT
    if conv.get("repo_full"):
        system += f"\n当前对话针对仓库：{conv['repo_full']}。回答时请围绕该仓库的实际情况。"
    history = [{"role": "system", "content": system}]
    for msg in (conv.get("messages") or [])[-40:]:
        history.append({"role": msg["role"], "content": msg["content"]})

    reply, model = complete(history)
    conv = append(cid, "assistant", reply)
    return {"conversation": conv, "reply": reply, "model": model}
