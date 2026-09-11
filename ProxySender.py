"""发信组件。

职责：
1. 向信令服务器申请一次代理会话，拿到对方（接收组件）的公网 / 内网 IP:端口；
2. 按候选地址**打洞**（默认 10 次），全失败转**服务端中继**；
   中继连续失败 2 次会自动重新分配一个代连方；
3. 隧道建立后把 socket 交给调用方——本地 CONNECT 代理（centre.proxy）用它承载
   git / requests 的 TLS 流量。

由 main.py 启动（启动即把 open_tunnel 注册给 centre.proxy）；也可单独运行：
python ProxySender.py
"""
import socket
import time

from centre import paths, proxy, remote

MAX_RELAY_FAILS = proxy.RELAY_MAX_FAILS


def ensure_started() -> bool:
    """把发信能力注册给公共模块，本地 CONNECT 代理即可复用。"""
    proxy.set_opener(open_tunnel)
    proxy.log("发信组件已就绪")
    return True


# ---------------------------------------------------------------- 信令
def request_session(exclude=None) -> dict:
    """向信令服务器申请会话，换取对方候选地址。"""
    body, _ = remote.call("/api/proxy/request", {
        "candidates": [f"{h}:{proxy.status().get('helper_port')}" for h in proxy.lan_hosts()],
        "attempts": proxy.PUNCH_ATTEMPTS,
        "exclude": exclude or [],
    }, timeout=25)
    if not body.get("ok"):
        raise proxy.ProxyError(body.get("reason") or "当前没有可用的代连节点")
    return body["session"]


def report(sid: str, payload: dict) -> dict:
    """上报打洞 / 中继结果；服务端会返回是否需要重新分配。"""
    try:
        body, _ = remote.call("/api/proxy/report", {"sid": sid, **payload}, timeout=15)
        return body
    except Exception:
        return {}


def handshake(sock: socket.socket, sid: str, token: str, role: str, target: str) -> dict:
    """发信：握手建立隧道。"""
    proxy.send_line(sock, {"sid": sid, "token": token, "role": role, "target": target})
    reply = proxy.read_line(sock, timeout=20)
    if not reply.get("ok"):
        raise proxy.ProxyError(reply.get("reason") or "隧道建立失败")
    return reply


# ---------------------------------------------------------------- 打洞 / 中继
def open_tunnel(target: str, exclude=None) -> tuple[socket.socket, str]:
    """建立到 target 的隧道，返回 (socket, 'direct'|'relay')。"""
    if target not in proxy.ALLOWED_TARGETS:
        raise proxy.ProxyError(f"目标不在白名单内：{target}")
    session = request_session(exclude)
    sid, token = session["sid"], session["token"]
    candidates = session.get("candidates") or []
    attempts = int(session.get("attempts") or proxy.PUNCH_ATTEMPTS)

    # 1) 打洞：按候选地址轮流尝试 attempts 次
    tried = 0
    for i in range(attempts if candidates else 0):
        hostport = candidates[i % len(candidates)]
        host, port = proxy.split_hostport(hostport)
        tried += 1
        try:
            sock = socket.create_connection((host, port), timeout=proxy.PUNCH_TIMEOUT)
            handshake(sock, sid, token, "requester", target)
            report(sid, {"punch_attempts": tried, "direct_ok": True})
            proxy.bump("borrow_count")
            proxy.bump("direct_ok")
            proxy.log(f"打洞成功（第 {tried} 次，{hostport}）")
            return sock, "direct"
        except Exception:
            time.sleep(0.3)
    report(sid, {"punch_attempts": tried})

    # 2) 中继兜底
    relay_host = (session.get("relay_host") or "").strip() or proxy.remote_hostname()
    relay_port = int(session.get("relay_port") or 0)
    if not relay_port:
        raise proxy.ProxyError("服务端未开启中继端口（PROXY_RELAY_PORT）")

    excludes = list(exclude or [])
    relay_fails = 0
    while True:
        try:
            sock = socket.create_connection((relay_host, relay_port), timeout=12)
            handshake(sock, sid, token, "requester", target)
            proxy.bump("borrow_count")
            proxy.bump("relay_ok")
            proxy.log(f"打洞 {tried} 次失败，已改走服务端中继")
            return sock, "relay"
        except Exception:
            relay_fails += 1
            proxy.bump("relay_fails")
            res = report(sid, {"relay_fail": True})
            if relay_fails >= MAX_RELAY_FAILS or res.get("should_switch"):
                proxy.log(f"中继连续失败 {relay_fails} 次，重新分配代连方")
                helper = session.get("helper_email") or sid
                excludes.append(helper)
                session = request_session(excludes)
                sid, token = session["sid"], session["token"]
                relay_port = int(session.get("relay_port") or relay_port)
                relay_fails = 0
                report(sid, {"punch_attempts": tried})
                continue
            time.sleep(0.5)


# ---------------------------------------------------------------- 单独运行
def main() -> None:
    paths.ensure_dirs()
    ensure_started()
    print("发信组件已就绪（由本地 CONNECT 代理调用）。Ctrl+C 退出。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
