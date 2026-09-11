"""CodeVoyage 本地守护进程入口。

- centre.main() 在 5431 端口托管可视化控制台与 API（放在主线程，Flask 行为最稳）；
- GetIssue / Agent 作为后台线程运行；
- 启动后自动打开控制台（可用 CODEVOYAGE_NO_BROWSER=1 关闭）；
- 安装 pystray + Pillow 时提供系统托盘（打开控制台 / 退出）。
"""
import os
import threading
import time
import webbrowser

import centre
import GetIssue
import Agent
from centre import paths

CONSOLE_URL = "http://127.0.0.1:5431"


def _open_console() -> None:
    try:
        webbrowser.open(CONSOLE_URL)
    except Exception:
        pass


def _start_tray():
    """可选托盘：未安装依赖时自动跳过，退出可直接结束后台进程。"""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        paths.append_log("未安装 pystray/Pillow，跳过系统托盘；退出请直接结束后台进程（或 Ctrl+C）")
        return None

    image = Image.new("RGB", (64, 64), (13, 14, 17))
    ImageDraw.Draw(image).text((16, 24), "CV", fill=(76, 141, 255))

    def _quit(icon=None, item=None):
        try:
            icon.stop()
        except Exception:
            pass
        os._exit(0)

    icon = pystray.Icon(
        "CodeVoyage",
        image,
        "CodeVoyage 本地 Agent",
        menu=pystray.Menu(
            pystray.MenuItem("打开控制台", lambda: _open_console()),
            pystray.MenuItem("退出", _quit),
        ),
    )
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


if __name__ == "__main__":
    paths.ensure_dirs()
    info = paths.storage_info()
    paths.append_log(f"启动：本地存储 {info['base_dir']}（来源 {info['source']}，可写={info['writable']}）")
    print(f"本地存储目录：{info['base_dir']}（来源 {info['source']}）")
    threading.Thread(target=GetIssue.main, daemon=True).start()
    threading.Thread(target=Agent.main, daemon=True).start()
    # 凭据（GitHub Token / LLM）存服务端：启动时后台迁移旧配置并同步缓存
    from centre import credentials

    threading.Thread(target=credentials.startup, daemon=True).start()
    # GitHub 代理：两个组件都由 main 启动
    #   发信组件（ProxySender）：向信令服务器要对方 IP、打洞、发信建隧道
    #   接收组件（ProxyReceiver）：监听端口、登记自己的地址、替对方盲转发
    # 安全默认：借用他人代连（enabled）与「本机作为代连节点」（as_helper）**默认关闭**，
    # 需在控制台显式开启；开启代连节点还必须带明确授权（confirm），避免无感地对外暴露端口。
    from centre import proxy as cv_proxy
    import ProxyReceiver
    import ProxySender

    threading.Thread(target=ProxySender.ensure_started, daemon=True).start()
    if cv_proxy.helper_authorized():
        threading.Thread(target=ProxyReceiver.ensure_started, daemon=True).start()
    else:
        cv_proxy.log("代连节点默认关闭：本机不监听转发端口，也不向信令服务器登记")
    if cv_proxy.status().get("enabled"):
        threading.Thread(target=cv_proxy.local_start, daemon=True).start()
    if not os.getenv("CODEVOYAGE_NO_BROWSER"):
        threading.Thread(target=lambda: (time.sleep(1.5), _open_console()), daemon=True).start()
    _start_tray()
    print(f"CodeVoyage 控制台：{CONSOLE_URL}")
    print("本进程为常驻后台：关闭浏览器页面不影响运行；退出请用托盘「退出」（Ctrl+C 亦可）。")
    centre.main()
