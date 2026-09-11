"""GitHub 代理：公共协议、状态与本地 CONNECT 代理。

两个客户端组件分别落在独立文件、由 main.py 启动（也可单独运行）：
- 接收组件 ProxyReceiver.py：监听端口、向信令服务器登记、代其它客户端盲转发
- 发信组件 ProxySender.py：向信令服务器要对方 IP、打洞、发信建立隧道

本模块只放两边共用的东西：常量、协议编解码、状态与统计、代理模式开关，
以及给 git / requests 用的本地 CONNECT 代理（它通过发信组件的 open_tunnel 建隧道）。

数据流向：
    请求方 git/requests → 本地 CONNECT 代理 → 发信组件打洞/中继 → 接收组件 → github.com:443
TLS 端到端加密，接收方只搬运密文，看不到 Token 与代码。

安全策略（审计整改）：
- **默认全部关闭**：`enabled`（借用他人代连）与 `as_helper`（本机作为代连节点，
  会把端口暴露到局域网/公网）都需要用户在控制台显式开启；开启代连节点还必须带
  `confirm=True`，属于「知情授权」，启动时写入审计日志。
- **目标白名单**：只允许 ALLOWED_TARGETS（可用 CODEVOYAGE_PROXY_TARGETS 追加，
  逗号分隔）中的 `host:port`，其余一律 403；且域名解析结果不得是私网 / 回环 /
  链路本地 / 保留地址，避免被当成访问内网的跳板。
- **本地代理只监听 127.0.0.1**，并校验来源必须是回环地址。
- 代连节点限制并发会话数（max_sessions），每条会话都写审计日志（来源 IP + 目标）。
"""
import ipaddress
import json
import os
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
MAX_HELPER_SESSIONS = 4   # 代连节点同时承载的会话上限

_lock = threading.RLock()
_state = {
    "enabled": False,          # 使用代理（请求方）：默认关闭，需显式开启
    "as_helper": False,        # 作为代连节点（接收方）：默认关闭，需显式授权
    "helper_consent": False,   # 是否已获得「作为代连节点」的明确授权
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
    "active_sessions": 0,
    "max_sessions": MAX_HELPER_SESSIONS,
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


def configure(enabled=None, as_helper=None, helper_port=None, local_port=None,
              confirm: bool = False) -> dict:
    """修改代理配置。

    as_helper=True 必须同时传 confirm=True：把本机变成代连节点意味着对其它客户端
    开放一个转发端口（可被用于借道出网），属于敏感开关，要求调用方显式确认。
    """
    note = ""
    with _lock:
        if enabled is not None:
            _state["enabled"] = bool(enabled)
            note = f"代理（借用他人代连）{'已开启' if _state['enabled'] else '已关闭'}"
        if as_helper is not None:
            if bool(as_helper) and not confirm:
                _state["fail_reason"] = "开启代连节点需要显式确认（confirm=true）"
                note = "代连节点未开启：缺少显式确认"
            elif bool(as_helper):
                _state["as_helper"] = True
                _state["helper_consent"] = True
                note = "已开启代连节点（本机会向信令服务器登记并代其它客户端转发白名单 TLS 流量）"
            else:
                _state["as_helper"] = False
                _state["helper_consent"] = False
                note = "代连节点已关闭"
        if helper_port:
            _state["helper_port"] = int(helper_port)
        if local_port:
            _state["local_port"] = int(local_port)
    if note:
        _log(f"[审计] {note}")
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


def helper_authorized() -> bool:
    """是否已获得代连节点的明确授权（默认未授权）。"""
    with _lock:
        return bool(_state["as_helper"] and _state["helper_consent"])


def _log(msg: str) -> None:
    paths.append_log(f"[代理] {msg}")
    with _lock:
        _state["last_event"] = msg


# 供 ProxyReceiver / ProxySender 两个组件使用
log = _log


# ---------------------------------------------------------------- 目标白名单
def allowed_targets() -> set:
    """白名单目标集合；可用 CODEVOYAGE_PROXY_TARGETS 追加（逗号分隔的 host:port）。"""
    out = set(ALLOWED_TARGETS)
    for item in str(os.getenv("CODEVOYAGE_PROXY_TARGETS") or "").split(","):
        target = item.strip().lower()
        if target and ":" in target:
            out.add(target)
    return out


def _is_public_host(host: str) -> bool:
    """域名解析结果必须全部是公网地址（拒绝私网 / 回环 / 链路本地 / 保留地址）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def is_allowed_target(target: str) -> bool:
    """目标是否合法：必须在白名单内，且解析出的地址都是公网地址。"""
    text = str(target or "").strip().lower()
    if text not in allowed_targets():
        return False
    host, port = split_hostport(text)
    if not host or not port:
        return False
    return _is_public_host(host)


# ---------------------------------------------------------------- 代连会话配额
def session_open() -> bool:
    """申请一个代连会话名额；超出并发上限返回 False。"""
    with _lock:
        if int(_state["active_sessions"]) >= int(_state["max_sessions"]):
            return False
        _state["active_sessions"] = int(_state["active_sessions"]) + 1
        return True


def session_close() -> None:
    with _lock:
        _state["active_sessions"] = max(0, int(_state["active_sessions"]) - 1)


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


def _is_loopback_addr(addr) -> bool:
    try:
        ip = ipaddress.ip_address(str(addr[0]))
    except (ValueError, IndexError, TypeError):
        return False
    return ip.is_loopback


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
        srv.bind(("127.0.0.1", port))    # 只监听本机回环，不对局域网暴露
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
            conn, addr = srv.accept()
        except OSError:
            return
        if not _is_loopback_addr(addr):     # 双保险：非本机来源直接断开
            try:
                conn.close()
            except OSError:
                pass
            continue
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
        target = parts[1].strip().lower()
        if not is_allowed_target(target):
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
    """按需启动接收组件、发信组件与本地代理（幂等）。

    代连节点（接收组件）只在「用户显式授权」时才启动：默认不对外暴露端口、
    不向信令服务器登记。
    """
    import ProxyReceiver
    import ProxySender

    ProxySender.ensure_started()
    with _lock:
        want_helper = helper_authorized()
        want_local = _state["enabled"]
    if want_helper:
        ProxyReceiver.ensure_started()
    else:
        ProxyReceiver.stop()
        log("代连节点未授权（默认关闭），本机不会向信令服务器登记")
    if want_local:
        local_start()
    else:
        local_stop()
    return status()


def shutdown() -> None:
    import ProxyReceiver

    ProxyReceiver.stop()
    local_stop()
