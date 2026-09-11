"""右下角确认弹窗（原生窗口）。

后台收到 Issue、Agent 进入"等待确认"阶段时调用 ask()，屏幕右下角弹出一个置顶小窗，
可直接点「执行」/「放弃」；不操作则倒计时结束，由 Agent 按原逻辑自动执行。

实现要点：
- Tkinter 只能在创建它的那个线程里调用，因此单独起一个线程持有 Tk root 并跑 mainloop，
  其它线程只通过队列投递请求；
- 无 tkinter 或初始化失败时自动降级为只写日志，不影响主流程。
"""
import queue
import threading

from centre import core, paths

WIDTH = 384
HEIGHT = 176
MARGIN = 18
BOTTOM_GAP = 60          # 抬高一些，避开任务栏
STACK_GAP = 10           # 同时出现多个窗口时的间距

BG = "#171a20"
BORDER = "#2a2f3a"
TEXT = "#e8eaed"
DIM = "#9aa4b2"
ACCENT = "#4c8dff"
DANGER = "#e05d5d"
FONT = "Microsoft YaHei UI"

_queue: "queue.Queue[dict]" = queue.Queue()
_boot_lock = threading.Lock()
_booted = False


def available() -> bool:
    """是否有可用的 tkinter。"""
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


def ask(uuid: str, repo_full: str, issue_number, title: str, timeout: float = 10.0) -> None:
    """请求弹出确认窗口（非阻塞；用户操作会写入 core 的任务决断）。"""
    if not available():
        paths.append_log("未安装 tkinter，跳过右下角确认弹窗")
        return
    _start()
    _queue.put({
        "uuid": uuid,
        "repo_full": repo_full,
        "issue_number": issue_number,
        "title": title or "",
        "timeout": float(timeout),
    })


def _start() -> None:
    global _booted
    with _boot_lock:
        if _booted:
            return
        _booted = True
    threading.Thread(target=_ui_loop, name="CodeVoyagePopup", daemon=True).start()


def _ui_loop() -> None:
    try:
        import tkinter as tk
    except Exception as e:
        paths.append_log(f"弹窗不可用（缺少 tkinter）：{e}")
        return
    try:
        root = tk.Tk()
    except Exception as e:
        paths.append_log(f"弹窗初始化失败：{e}")
        return
    root.withdraw()
    state = {"count": 0}

    def poll():
        try:
            while True:
                req = _queue.get_nowait()
                try:
                    _show(tk, root, state, req)
                except Exception as e:
                    paths.append_log(f"弹窗显示失败：{e}")
        except queue.Empty:
            pass
        try:
            root.after(250, poll)
        except Exception:
            pass

    root.after(250, poll)
    try:
        root.mainloop()
    except Exception as e:
        paths.append_log(f"弹窗线程退出：{e}")


def _show(tk, root, state: dict, req: dict) -> None:
    index = state["count"]
    state["count"] += 1

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=BORDER)

    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = max(MARGIN, sw - WIDTH - MARGIN)
    y = max(MARGIN, sh - HEIGHT - BOTTOM_GAP - index * (HEIGHT + STACK_GAP))
    win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=1, pady=1)

    head = tk.Frame(body, bg=BG)
    head.pack(fill="x", padx=14, pady=(12, 4))
    tk.Label(head, text="CodeVoyage 收到新任务", bg=BG, fg=ACCENT,
             font=(FONT, 10, "bold")).pack(side="left")
    tip = tk.Label(head, text="", bg=BG, fg=DIM, font=(FONT, 9))
    tip.pack(side="right")

    tk.Label(body, text=f'{req["repo_full"]}#{req["issue_number"]}', bg=BG, fg=TEXT,
             font=(FONT, 9), anchor="w").pack(fill="x", padx=14)
    tk.Label(body, text=(req["title"] or "（无标题）")[:80] or "（无标题）", bg=BG, fg=DIM,
             font=(FONT, 9), anchor="w", justify="left",
             wraplength=WIDTH - 32).pack(fill="x", padx=14, pady=(4, 0))

    closed = {"v": False}

    def close():
        if closed["v"]:
            return
        closed["v"] = True
        state["count"] = max(0, state["count"] - 1)
        try:
            win.destroy()
        except Exception:
            pass

    def choose(yes: bool):
        try:
            core.decide(req["uuid"], yes)
        except Exception as e:
            paths.append_log(f"弹窗决断写入失败：{e}")
        paths.append_log(
            f"弹窗确认 {req['repo_full']}#{req['issue_number']}：{'执行' if yes else '放弃'}"
        )
        close()

    actions = tk.Frame(body, bg=BG)
    actions.pack(fill="x", padx=14, pady=(12, 12))
    tk.Button(actions, text="执行", command=lambda: choose(True), bg=ACCENT, fg="#ffffff",
              activebackground=ACCENT, activeforeground="#ffffff", relief="flat", bd=0,
              font=(FONT, 9), padx=18, pady=4, cursor="hand2").pack(side="left")
    tk.Button(actions, text="放弃", command=lambda: choose(False), bg=BG, fg=DANGER,
              activebackground=BG, activeforeground=DANGER, relief="flat", bd=0,
              highlightthickness=1, highlightbackground=DANGER, highlightcolor=DANGER,
              font=(FONT, 9), padx=18, pady=4, cursor="hand2").pack(side="left", padx=(10, 0))

    remain = {"t": float(req.get("timeout") or 10)}

    def tick():
        if closed["v"]:
            return
        remain["t"] -= 0.5
        if remain["t"] <= 0:
            # 到点不做决断：Agent 侧超时会按原逻辑自动执行
            close()
            return
        tip.config(text=f"{int(remain['t'] + 0.999)}s 后自动执行")
        try:
            win.after(500, tick)
        except Exception:
            pass

    tip.config(text=f"{int(remain['t'])}s 后自动执行")
    win.after(500, tick)
