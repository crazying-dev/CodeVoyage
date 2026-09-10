"""CodeVoyage 本地守护进程入口。

- centre.main() 在 5431 端口托管可视化控制台与 API（放在主线程，Flask 行为最稳）；
- GetIssue / Agent 作为后台线程运行。
"""
import threading

import centre
import GetIssue
import Agent

if __name__ == "__main__":
    threading.Thread(target=GetIssue.main, daemon=True).start()
    threading.Thread(target=Agent.main, daemon=True).start()
    centre.main()
