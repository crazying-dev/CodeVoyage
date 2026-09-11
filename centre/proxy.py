"""GitHub 代理：公共协议、状态与本地 CONNECT 代理。

两个客户端组件分别落在独立文件、由 main.py 启动（也可单独运行）：
- 接收组件 ProxyReceiver.py：监听端口、向信令服务器登记、代其它客户端盲转发
- 发信组件 ProxySender.py：向信令服务器要对方 IP、打洞、发信建立隧道

本模块只放两边共用的东西：常量、协议编解码、状态与统计、代理模式开关，
以及给 git / requests 用的本地 CONNECT 代理（它通过发信组件的 open_tunnel 建隧道）。

数据流向：
    请求方 git/requests → 本地 CONNECT 代理 → 发信组件打洞/中继 → 接收组件 → github.com:443
TLS 端到端加密，接收方只搬运密文，看不到 Token 与代码。
"""
import json
import socket
import threading

from centre import net, paths

PROTO = "CVTUN/1"
HANDSHAKE_LIMIT = 4096
ALLOWED_TARGETS = {
    "github.com:443",
    "api.github.com:443",
    "codeload.github.com:443",
    "raw.githubusercontent.com:443",
}
PUNCH_ATTEMPTS = 10       # 打洞尝试次数，用尽转中继
RELAY_MAX_FAILS = 2       # 中继连续失败次数，达到则重新分配代连方
PUNCH_TIMEOUT = 3.0
HELPER_PORT_DEFAULT = 45871
LOCAL_PROXY_PORT_DEFAULT = 45872
HEARTBEAT_INTERVAL = 30

_lock = threading.RLock()
_state = {
    "enabled": True,           # 使用代理（请求方）
    "as_helper": True,         # 作为代连节点（接收方）
    "helper_port": HELPER_PORT_DEFAULT,
    "local_port": LOCAL_PROXY_PORT_DEFAULT,
    "node_id": "",
    "helper_running": False,
    "local_running": False,
    "sender_ready": False,
    "mode": "direct",          # direct | proxy
    "assist_count": 0,
    "borrow_count": 0,
    "direct_ok": 0,
    "relay_ok": 0,
    "relay_fails": 0,
    "fail_reason": "",
    "last_event": "",
}
_servers = {"local": None}
_opener = None


class ProxyError(RuntimeError):
    pass


# ---------------------------------------------------------------- 状态
def status() -> dict:
    with _lock:
        return dict(_state)


def configure(enabled=None, as_helper=None, helper_port=None, local_port=None) -> dict:
    with _lock:
        if enabled is not None:
            _state["enabled"] = bool(enabled)
        if as_helper is not None:
            _state["as_helper"] = bool(as_helper)
        if helper_port:
            _state["helper_port"] = int(helper_port)
        if local_port:
            _state["local_port"] = int(local_port)
    return status()


def bump(key: str, delta: int = 1) -> None:
    with _lock:
        _state[key] = int(_state.get(key) or 0) + delta


def set_state(**patch) -> None:
    with _lock:
        _state.update(patch)


def local_proxy_url() -> str:
    with _lock:
        return f"http://127.0.0.1:{_state['local_port']}"


def active() -> bool:
    with _lock:
        return bool(_state["enabled"] and _state["local_running"])


def _log(msg: str) -> None:
    paths.append_log(f"[代理] {msg}")
    with _lock:
        _state["last_event"] = msg


# 供 ProxyReceiver / ProxySender 两个组件使用
log = _log


# ---------------------------------------------------------------- 代理模式
def in_proxy_mode() -> bool:
    with _lock:
        return bool(_state.get("mode") == "proxy")


def enter_proxy_mode() -> bool:
    """把走环境变量的调用（git、PyGithub）切到本地代理。"""
    import os

    if not active():
        return False
    url = local_proxy_url()
    os.environ["HTTPS_PROXY"] = os.environ["https_proxy"] = url
    os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = url
    set_state(mode="proxy")
    return True


def exit_proxy_mode() -> None:
    import os

    for key in ("HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "https_proxy"):
        os.environ.pop(key, None)
    set_state(mode="direct")


def _is_network_error(err) -> bool:
    text = str(err).lower()
    return any(k in text for k in (
        "connect", "timeout", "timed out", "reset", "unable to access",
        "network", "proxy", "eof", "refused", "unreachable",
    ))


def github_call(fn, *args, **kwargs):
    """执行 GitHub 相关调用；直连遇到网络类失败时，自动切代理重试一次。"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if not _is_network_error(e) or not active() or in_proxy_mode():
            raise
        _log(f"直连 GitHub 失败（{str(e)[:80]}），改走代理重试")
        if not enter_proxy_mode():
            raise
        try:
            return fn(*args, **kwargs)
        finally:
            exit_proxy_mode()


# ---------------------------------------------------------------- 协议工具
def lan_hosts() -> list:
    hosts = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in hosts:
                hosts.append(ip)
    except OSError:
        pass
    return hosts[:4]


def send_line(sock: socket.socket, payload: dict) -> None:
    sock.sendall((PROTO + " " + json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def read_line(sock: socket.socket, timeout: float = 15) -> dict:
    buf = b""
    sock.settimeout(timeout)
    while b"\n" not in buf and len(buf) < HANDSHAKE_LIMIT:
        chunk = sock.recv(1024)
        if not chunk:
            break
        buf += chunk
    text = buf.decode("utf-8", "replace").strip()
    if not text.startswith(PROTO):
        raise ProxyError("协议头不匹配")
    return json.loads(text[len(PROTO):].strip() or "{}")


def pipe(a: socket.socket, b: socket.socket) -> None:
    """双向盲转发直到任一端关闭。"""
    def _one(src, dst):
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


def split_hostport(hostport: str) -> tuple[str, int]:
    host, _, port = str(hostport).rpartition(":")
    return host, int(port or 0)


def remote_hostname() -> str:
    return paths.remote_base().split("://", 1)[-1].split("/")[0].partition(":")[0]


# ---------------------------------------------------------------- 发信组件入口（注册制，避免循环依赖）
def set_opener(fn) -> None:
    global _opener
    _opener = fn
    set_state(sender_ready=bool(fn))


def open_tunnel(target: str, exclude=None):
    """建立到 target 的隧道，返回 (socket, 'direct'|'relay')。由发信组件提供实现。"""
    if _opener is None:
        raise ProxyError("发信组件未启动（ProxySender）")
    return _opener(target, exclude)


# ---------------------------------------------------------------- 本地 CONNECT 代理
def local_start(port: int | None = None) -> bool:
    with _lock:
        if _state["local_running"]:
            return True
        port = int(port or _state["local_port"])
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(32)
    except OSError as e:
        _log(f"本地代理启动失败（{port}）：{e}")
        return False
    _servers["local"] = srv
    set_state(local_running=True, local_port=port)
    threading.Thread(target=_local_accept, args=(srv,), daemon=True, name="ProxyLocal").start()
    _log(f"本地代理已就绪：{local_proxy_url()}")
    return True


def local_stop() -> None:
    srv = _servers.pop("local", None)
    set_state(local_running=False)
    if srv:
        try:
            srv.close()
        except OSError:
            pass


def _local_accept(srv: socket.socket) -> None:
    while True:
        with _lock:
            if not _state["local_running"]:
                return
        try:
            conn, _addr = srv.accept()
        except OSError:
            return
        threading.Thread(target=_local_session, args=(conn,), daemon=True).start()


def _local_session(conn: socket.socket) -> None:
    """处理一条 CONNECT 请求：白名单目标走隧道，其余直接拒绝。"""
    try:
        conn.settimeout(20)
        buf = b""
        while b"\r\n" not in buf and len(buf) < 8192:
            chunk = conn.recv(1024)
            if not chunk:
                break
            buf += chunk
        head = buf.split(b"\r\n", 1)[0].decode("utf-8", "replace")
        parts = head.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            conn.close()
            return
        target = parts[1].strip()
        if target not in ALLOWED_TARGETS:
            set_state(fail_reason=f"目标不在白名单内：{target}")
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            conn.close()
            return
        try:
            tunnel, _via = open_tunnel(target)
        except Exception as e:
            set_state(fail_reason=str(e))
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            conn.close()
            _log(f"隧道建立失败：{e}")
            return
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    except OSError:
        try:
            conn.close()
        except OSError:
            pass
        return
    leftover = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""
    if leftover:
        try:
            tunnel.sendall(leftover)
        except OSError:
            pass
    pipe(conn, tunnel)


# ---------------------------------------------------------------- 编排
def ensure_started() -> dict:
    """按需启动接收组件、发信组件与本地代理（幂等）。"""
    import ProxyReceiver
    import ProxySender

    ProxySender.ensure_started()
    with _lock:
        want_helper = _state["as_helper"]
        want_local = _state["enabled"]
    if want_helper:
        ProxyReceiver.ensure_started()
    else:
        ProxyReceiver.stop()
    if want_local:
        local_start()
    else:
        local_stop()
    return status()


def shutdown() -> None:
    import ProxyReceiver

    ProxyReceiver.stop()
    local_stop()
