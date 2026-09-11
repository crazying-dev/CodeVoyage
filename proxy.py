"""GitHub 代理：协调与中继（服务端）。

角色
- 协调：客户端登记为「可连 GitHub 的代连节点」；请求方领取一次代理会话，
  拿到代连方的候选地址（公网 / 内网）与中继地址。
- 中继：打洞失败时，两台客户端都连本机 relay 端口，由服务端**盲转发字节**。
  中继只搬运 TLS 密文，请求方的 GitHub Token 与代码不会暴露给代连方
  （代连方只知道"目标主机是 github.com"）。

会话流程
1. 请求方 POST /api/proxy/request → {sid, token, helper, candidates, relay, attempts}
2. 请求方按 candidates 打洞（最多 attempts 次）；代连方同时回拨请求方的候选地址
3. 任一方连通即建立隧道；都失败则双方各自连 relay 端口，用 sid+token 配对后转发
"""
import json
import socket
import threading
import time
import uuid

NODE_TTL = 90            # 节点心跳超时（秒）
SESSION_TTL = 180        # 会话有效期
DEFAULT_PUNCH_ATTEMPTS = 10   # 打洞尝试次数（用尽转中继）
MAX_RELAY_FAILS = 2      # 中继连续失败次数，达到则重新分配代连方
PROTO = "CVTUN/1"
HANDSHAKE_LIMIT = 4096

_lock = threading.RLock()
_nodes: dict = {}        # node_id -> node
_sessions: dict = {}     # sid -> session
_relay = {"sock": None, "port": 0, "running": False}


# ---------------------------------------------------------------- 工具
def _now() -> float:
    return time.time()


def _gc() -> None:
    """清理超时节点与会话。"""
    with _lock:
        for nid in [k for k, v in _nodes.items() if _now() - v["updated_at"] > NODE_TTL]:
            _nodes.pop(nid, None)
        for sid in [k for k, v in _sessions.items() if _now() - v["created_at"] > SESSION_TTL]:
            _sessions.pop(sid, None)


def _send_line(sock: socket.socket, payload: dict) -> None:
    sock.sendall((PROTO + " " + json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def _read_line(sock: socket.socket) -> dict:
    buf = b""
    sock.settimeout(15)
    while b"\n" not in buf and len(buf) < HANDSHAKE_LIMIT:
        chunk = sock.recv(1024)
        if not chunk:
            break
        buf += chunk
    text = buf.decode("utf-8", "replace").strip()
    if not text.startswith(PROTO):
        raise ValueError("协议头不匹配")
    return json.loads(text[len(PROTO):].strip() or "{}")


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """双向盲转发直到任一端关闭。"""
    def _one(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    t = threading.Thread(target=_one, args=(a, b), daemon=True)
    t.start()
    _one(b, a)
    t.join(timeout=5)


# ---------------------------------------------------------------- 节点登记
def register_node(user_id, email, public_host, port, lan_hosts, github_ok, note="") -> dict:
    _gc()
    node_id = uuid.uuid4().hex[:16]
    node = {
        "node_id": node_id,
        "user_id": user_id,
        "email": email or "",
        "public_host": public_host or "",
        "port": int(port or 0),
        "lan_hosts": [h for h in (lan_hosts or []) if h][:4],
        "github_ok": bool(github_ok),
        "note": note or "",
        "updated_at": _now(),
        "created_at": _now(),
        "used": 0,
    }
    with _lock:
        _nodes[node_id] = node
    return node


def touch_node(node_id: str, user_id) -> bool:
    _gc()
    with _lock:
        node = _nodes.get(node_id)
        if not node or node["user_id"] != user_id:
            return False
        node["updated_at"] = _now()
        return True


def unregister_node(node_id: str, user_id) -> bool:
    with _lock:
        node = _nodes.get(node_id)
        if not node or node["user_id"] != user_id:
            return False
        _nodes.pop(node_id, None)
        return True


def list_nodes(user_id=None) -> list:
    _gc()
    with _lock:
        items = []
        for n in _nodes.values():
            items.append({
                "node_id": n["node_id"],
                "email": n["email"],
                "port": n["port"],
                "github_ok": n["github_ok"],
                "mine": n["user_id"] == user_id,
                "idle_seconds": int(_now() - n["updated_at"]),
            })
        return items


def _pick_helper(user_id, exclude_emails=None) -> dict | None:
    """优先同账号的其它设备，其次其它用户；都不可用返回 None。"""
    with _lock:
        pool = [n for n in _nodes.values() if n["github_ok"]]
        if not pool:
            return None
        same = [n for n in pool if n["user_id"] == user_id]
        others = [n for n in pool if n["user_id"] != user_id]
        same.sort(key=lambda n: n["used"])
        others.sort(key=lambda n: n["used"])
        return (same or others or [None])[0]


def _candidates(node: dict) -> list:
    out = []
    if node.get("public_host") and node.get("port"):
        out.append(f"{node['public_host']}:{node['port']}")
    for host in node.get("lan_hosts") or []:
        if node.get("port"):
            out.append(f"{host}:{node['port']}")
    seen = []
    for item in out:
        if item not in seen:
            seen.append(item)
    return seen


def create_session(user_id, requester_host="", requester_candidates=None,
                   attempts: int = DEFAULT_PUNCH_ATTEMPTS, exclude_helpers=None) -> dict | None:
    """为请求方分配一个代连节点并创建会话。"""
    _gc()
    helper = _pick_helper(user_id, exclude_helpers)
    if not helper:
        return None
    helper["used"] += 1
    sid = uuid.uuid4().hex
    session = {
        "sid": sid,
        "token": uuid.uuid4().hex,
        "requester_id": user_id,
        "helper_id": helper["node_id"],
        "helper_user_id": helper["user_id"],
        "helper_email": helper["email"],
        "requester_host": requester_host or "",
        "requester_candidates": list(requester_candidates or []),
        "candidates": _candidates(helper),
        "attempts": max(1, int(attempts or DEFAULT_PUNCH_ATTEMPTS)),
        "same_user": helper["user_id"] == user_id,
        "created_at": _now(),
        "direct_ok": False,
        "relay_ok": False,
        "relay_fails": 0,
        "switched": 0,
        "punch_attempts": 0,
        "done": False,
    }
    with _lock:
        _sessions[sid] = session
    return session


def get_session(sid: str) -> dict | None:
    with _lock:
        return _sessions.get(sid)


def verify_session(sid: str, token: str, role: str) -> tuple[bool, str]:
    """校验会话凭据与角色（代连方 / 请求方）。"""
    with _lock:
        s = _sessions.get(sid)
    if not s:
        return False, "会话不存在或已过期"
    if token != s["token"]:
        return False, "凭据不匹配"
    if role == "helper" and s.get("done"):
        return False, "会话已结束"
    return True, ""


def report(sid: str, outcome: dict) -> dict | None:
    """记录打洞 / 中继结果；中继连续失败达到阈值时给出「重新分配」建议。"""
    with _lock:
        s = _sessions.get(sid)
        if not s:
            return None
        if "punch_attempts" in outcome:
            s["punch_attempts"] = int(outcome["punch_attempts"] or 0)
        if outcome.get("direct_ok"):
            s["direct_ok"] = True
            s["done"] = True
        if outcome.get("relay_ok"):
            s["relay_ok"] = True
            s["done"] = True
        if outcome.get("relay_fail"):
            s["relay_fails"] = int(s.get("relay_fails", 0)) + 1
        if s["done"]:
            s["finished_at"] = _now()
        return {
            "sid": s["sid"],
            "direct_ok": s["direct_ok"],
            "relay_ok": s["relay_ok"],
            "relay_fails": s["relay_fails"],
            "should_switch": s["relay_fails"] >= MAX_RELAY_FAILS,
            "done": s["done"],
        }


def pending_sessions(node_id: str) -> list:
    """该代连节点尚未完成的会话（供其轮询后主动回拨请求方）。"""
    _gc()
    with _lock:
        return [
            {
                "sid": s["sid"],
                "token": s["token"],
                "requester_candidates": s.get("requester_candidates") or [],
                "created_at": s["created_at"],
                "punch_attempts": s.get("punch_attempts", 0),
            }
            for s in _sessions.values()
            if s["helper_id"] == node_id and not s.get("done")
        ]


def stats() -> dict:
    _gc()
    with _lock:
        sessions = list(_sessions.values())
    return {
        "nodes": len(_nodes),
        "sessions": len(sessions),
        "direct_ok": sum(1 for s in sessions if s.get("direct_ok")),
        "relay_ok": sum(1 for s in sessions if s.get("relay_ok")),
        "relay_fails": sum(int(s.get("relay_fails") or 0) for s in sessions),
        "same_user": sum(1 for s in sessions if s.get("same_user")),
        "cross_user": sum(1 for s in sessions if not s.get("same_user")),
        "punch_attempt_total": sum(int(s.get("punch_attempts") or 0) for s in sessions),
        "relay_port": _relay["port"],
    }


# ---------------------------------------------------------------- 中继服务
def start_relay(port: int) -> bool:
    """启动中继监听：两台客户端按 sid 配对后盲转发。"""
    if not port or _relay["running"]:
        return False
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", int(port)))
        srv.listen(128)
    except OSError:
        return False
    _relay.update({"sock": srv, "port": int(port), "running": True})
    threading.Thread(target=_relay_accept, args=(srv,), daemon=True, name="ProxyRelay").start()
    return True


def stop_relay() -> None:
    srv = _relay.get("sock")
    _relay["running"] = False
    if srv:
        try:
            srv.close()
        except OSError:
            pass
    _relay["sock"] = None


def _relay_accept(srv: socket.socket) -> None:
    while _relay["running"]:
        try:
            conn, addr = srv.accept()
        except OSError:
            break
        threading.Thread(target=_relay_session, args=(conn, addr), daemon=True).start()


def _relay_session(conn: socket.socket, addr) -> None:
    """等待同一会话的请求方与代连方接入，配对后转发。"""
    try:
        hello = _read_line(conn)
    except Exception as e:
        try:
            _send_line(conn, {"ok": False, "reason": f"握手失败：{e}"})
        except OSError:
            pass
        conn.close()
        return

    sid = str(hello.get("sid") or "")
    role = str(hello.get("role") or "")
    ok, reason = verify_session(sid, str(hello.get("token") or ""), role)
    if not ok or role not in ("requester", "helper"):
        try:
            _send_line(conn, {"ok": False, "reason": reason or "角色不合法"})
        except OSError:
            pass
        conn.close()
        return

    peer, pair, is_piper = _wait_peer(sid, role, conn)
    if peer is None:
        try:
            _send_line(conn, {"ok": False, "reason": "配对超时，请重试"})
        except OSError:
            pass
        conn.close()
        return

    # 只有配对时后到的一方负责转发，避免两端重复复制同一对 socket
    if not is_piper:
        try:
            pair["done"].wait(timeout=180)
        finally:
            try:
                conn.close()
            except OSError:
                pass
        return

    try:
        _send_line(conn, {"ok": True, "via": "relay"})
        _send_line(peer, {"ok": True, "via": "relay"})
    except OSError:
        conn.close()
        peer.close()
        return
    try:
        _pipe(conn, peer)
    finally:
        pair["done"].set()


_pairs: dict = {}            # sid -> {"conns": {role: conn}, "ready": Event, "done": Event}
_pairs_lock = threading.RLock()


def _wait_peer(sid: str, role: str, conn: socket.socket):
    """配对两端。返回 (peer_conn, pair, is_piper)。

    - 后到的一方 is_piper=True：负责回握手并执行唯一的转发；
    - 先到的一方 is_piper=False：等转发结束后再返回，避免两端重复转发同一对 socket。
    """
    other = "helper" if role == "requester" else "requester"
    with _pairs_lock:
        pair = _pairs.get(sid)
        if pair is None:
            pair = {"conns": {}, "ready": threading.Event(), "done": threading.Event()}
            _pairs[sid] = pair
        pair["conns"][role] = conn
        ready = other in pair["conns"]
        if ready:
            _pairs.pop(sid, None)      # 已配对：清掉索引（对象仍被两端持有）
            pair["ready"].set()

    if ready:
        return pair["conns"].get(other), pair, True
    if not pair["ready"].wait(timeout=45):
        with _pairs_lock:
            if _pairs.get(sid) is pair:
                _pairs.pop(sid, None)
        return None, pair, False
    return pair["conns"].get(other), pair, False
