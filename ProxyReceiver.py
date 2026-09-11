"""接收组件（代连节点）。

职责：
1. 监听一个端口（"类似服务端的接口"），接受其它客户端的隧道请求；
2. 用自己的公网 IP/端口向信令服务器登记，并定时心跳（IP 由服务端从请求头取得）；
3. 校验对方会话凭据后，只连白名单目标（github.com:443 等）并**盲转发字节**——
   只搬运 TLS 密文，看不到对方的 Token 与代码。

安全（审计整改）：
- 未获得用户显式授权（centre.proxy.helper_authorized）时**不监听、不登记**；
- 目标必须同时通过白名单与「解析结果为公网地址」检查，避免被当作访问内网的跳板；
- 并发会话有上限（proxy.MAX_HELPER_SESSIONS），每条会话都写审计日志。

由 main.py 启动；也可单独运行：python ProxyReceiver.py
"""
import socket
import threading
import time

from centre import net, paths, proxy, remote

RETRY_INTERVAL = 20        # 登记失败后的重试间隔（秒）


class ReceiverError(RuntimeError):
    pass


_srv: socket.socket | None = None
_running = False
_lock = threading.RLock()


# ---------------------------------------------------------------- 启停
def ensure_started() -> bool:
    """启动监听并登记为代连节点（幂等）；未获授权则直接拒绝。"""
    global _srv, _running
    if not proxy.helper_authorized():
        proxy.log("代连节点未授权，忽略启动请求（本机不会监听/登记）")
        return False
    with _lock:
        if _running:
            return True
    port = int(proxy.status().get("helper_port") or proxy.HELPER_PORT_DEFAULT)
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(32)
    except OSError as e:
        proxy.log(f"接收组件监听失败（{port}）：{e}")
        return False
    with _lock:
        _srv, _running = srv, True
    proxy.set_state(helper_running=True, helper_port=port)
    proxy.log(f"[审计] 代连节点已启动，监听 {port}（仅转发白名单目标的 TLS 密文）")
    threading.Thread(target=_accept_loop, args=(srv,), daemon=True, name="ProxyReceiver").start()
    threading.Thread(target=_register_loop, daemon=True, name="ProxyRegister").start()
    threading.Thread(target=_heartbeat_loop, daemon=True, name="ProxyHeartbeat").start()
    return True


def stop() -> None:
    global _srv, _running
    with _lock:
        srv, _srv, _running = _srv, None, False
    node_id = str(proxy.status().get("node_id") or "")
    proxy.set_state(helper_running=False, node_id="")
    if srv:
        try:
            srv.close()
        except OSError:
            pass
    if node_id:
        try:
            remote.call("/api/proxy/unregister", {"node_id": node_id}, timeout=10)
        except Exception:
            pass


# ---------------------------------------------------------------- 信令：登记与心跳
def _register_loop() -> None:
    while True:
        if not proxy.status().get("helper_running"):
            return
        if not proxy.helper_authorized():   # 授权被撤销则立刻停止登记
            proxy.log("[审计] 代连授权已撤销，停止登记并退出")
            stop()
            return
        if not proxy.status().get("node_id"):
            try:
                github_ok = bool(net.latency("https://api.github.com/", timeout=6).get("ok"))
            except Exception:
                github_ok = False
            try:
                body, _ = remote.call("/api/proxy/register", {
                    "port": proxy.status().get("helper_port"),
                    "lan_hosts": proxy.lan_hosts(),
                    "github_ok": github_ok,
                }, timeout=20)
                proxy.set_state(node_id=body.get("node_id") or "")
                proxy.log(f"[审计] 已向信令服务器登记为代连节点（GitHub 可达={github_ok}）")
            except Exception as e:
                proxy.log(f"登记失败：{e}")
        time.sleep(RETRY_INTERVAL)


def _heartbeat_loop() -> None:
    while True:
        if not proxy.status().get("helper_running"):
            return
        node_id = str(proxy.status().get("node_id") or "")
        if node_id:
            try:
                body, _ = remote.call("/api/proxy/heartbeat", {"node_id": node_id}, timeout=15)
                if not body.get("ok"):
                    proxy.set_state(node_id="")     # 服务端已过期，交给登记线程重建
                else:
                    pending = body.get("pending") or []
                    if pending:
                        proxy.log(f"有 {len(pending)} 个代理请求待响应")
            except Exception:
                pass
        time.sleep(proxy.HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------- 监听与盲转发
def _accept_loop(srv: socket.socket) -> None:
    while True:
        if not proxy.status().get("helper_running"):
            return
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        threading.Thread(target=_session, args=(conn, addr), daemon=True).start()


def _reject(conn: socket.socket, reason: str) -> None:
    try:
        proxy.send_line(conn, {"ok": False, "reason": reason})
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def _session(conn: socket.socket, addr) -> None:
    """校验授权/凭据 → 白名单校验 → 回执 → 连目标 → 盲转发。

    先回执握手再连目标：连接 GitHub 可能要几秒，不能让对端把这段等待误判为失败。
    """
    if not proxy.helper_authorized():
        _reject(conn, "本机未授权作为代连节点")
        return
    try:
        hello = proxy.read_line(conn)
        sid = str(hello.get("sid") or "")
        token = str(hello.get("token") or "")
        target = str(hello.get("target") or "").strip().lower()
        if not proxy.is_allowed_target(target):
            raise ReceiverError("目标不在白名单内（或解析到非公网地址）")
        # 会话密钥字段名为 session_token：避免与登录令牌 token 冲突
        body, _ = remote.call("/api/proxy/verify",
                              {"sid": sid, "session_token": token, "role": "requester"},
                              timeout=15)
        if not body.get("ok"):
            raise ReceiverError(body.get("reason") or "凭据校验失败")
    except Exception as e:
        proxy.log(f"[审计] 拒绝来自 {addr[0] if addr else '?'} 的代连请求：{e}")
        _reject(conn, f"{e}")
        return

    if not proxy.session_open():
        proxy.log(f"[审计] 代连并发已达上限（{proxy.status().get('max_sessions')}），拒绝 {addr[0]}")
        _reject(conn, "代连节点繁忙")
        return

    try:
        try:
            proxy.send_line(conn, {"ok": True, "via": "helper", "target": target})
        except OSError:
            conn.close()
            return

        host, port = proxy.split_hostport(target)
        try:
            up = socket.create_connection((host, port), timeout=20)
        except OSError as e:
            proxy.log(f"代连连接 {target} 失败：{e}")
            try:
                conn.close()
            except OSError:
                pass
            return

        proxy.bump("assist_count")
        proxy.log(f"[审计] 为 {addr[0]} 代连 {target}（仅转发 TLS 密文）")
        proxy.pipe(conn, up)
    finally:
        proxy.session_close()


# ---------------------------------------------------------------- 单独运行
def main() -> None:
    paths.ensure_dirs()
    if not proxy.helper_authorized():
        raise SystemExit(
            "代连节点未授权：请先在控制台「代理」中显式开启（confirm），"
            "或在代码中调用 centre.proxy.configure(as_helper=True, confirm=True)。"
        )
    if not ensure_started():
        raise SystemExit("接收组件启动失败（端口被占用？）")
    print(f"接收组件运行中，监听 {proxy.status().get('helper_port')}；Ctrl+C 退出。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop()


if __name__ == "__main__":
    main()
