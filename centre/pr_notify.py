"""通知工作流（Issue #20）：任务结束后自动回复 Issue / PR。

背景：CodeVoyage 原来只有「自动处理 Issue」这一条链路 —— 任务成功 / 失败 / 用户放弃后，
Issue 提交者都收不到任何反馈（只能自己盯 PR 列表）。本模块补全通知能力：

- 成功：在 Issue 下回复，附 PR 链接、工作分支、结论摘要与 PR 可合并状态
  （含冲突是否已自动解决、没解决的原因），即 Issue 里要求的「PR 提交后自动回复」；
- 失败：在 Issue 下回复，附脱敏后的失败原因与重试提示；
- 放弃：在 Issue 下回复，说明用户取消、本次未改动任何内容；
- 有 PR 时额外在 PR 下留一条同样的通知，便于在 PR 页面直接看到结论。

边界与安全：
- 只写评论：不提供关闭 / 重开 Issue、加标签、改代码或改分支的能力；
- 正文先过 centre.sanitize：本机路径、令牌样式、带凭据 URL 一律替换后再发出
  （通知写到 GitHub，可能公开可见）；
- 令牌走本仓库（仓库专属 → 全局）顺序回退，不需要服务端配合即可工作；
- 幂等：同一任务的同类通知只发一次，记录在
  ~/.CodeVoyage/Agent/repo/<repo_hash>/notify.json；
- 可关闭：环境变量 CODEVOYAGE_DISABLE_PR_NOTIFY=1 或本地配置 pr_notify=false 时不发送；
- 通知失败只记日志 / 返回原因，绝不抛异常、绝不影响任务结果。
"""
import os
import threading
from datetime import datetime

from centre import paths, sanitize

# 通知类型：ok / failed / putout 对应 Agent 生命周期，manual 供 AI 工具主动回复
KINDS = ("ok", "failed", "putout", "manual")
TITLES = {"ok": "任务完成", "failed": "任务失败", "putout": "任务已放弃", "manual": "处理进度"}
TARGET_NAMES = {"issue": "Issue", "pr": "PR"}

MAX_RECORDS = 200        # 每个仓库最多保留的通知记录条数
CONCLUSION_LIMIT = 1200  # 结论摘要最多写入多少字
STEP_LIMIT = 8           # 通知里最多带多少条处理步骤

_lock = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    paths.append_log(f"[通知] {message}")


def _gh_issues():
    """延迟导入 GitHub Issue API，避免本模块被导入时就加载 PyGithub。"""
    from GithubTool import issues

    return issues


def _with_tokens(repo_full: str, action) -> tuple[bool, dict, str]:
    """按令牌顺序（仓库专属 → 全局）回退执行 action(token)。

    返回 (ok, data, reason)；全部失败时 ok=False，reason 为最后一次的原因。
    """
    candidates = paths.resolve_tokens(repo_full)
    if not candidates:
        return False, {}, "该仓库没有可用令牌"
    reason = ""
    last: dict = {}
    for token, _source in candidates:
        try:
            data = action(token) or {}
        except Exception as e:
            reason = str(e)
            continue
        last = data
        if data.get("ok"):
            return True, data, ""
        reason = data.get("reason") or reason
        if not data.get("retryable"):
            break
    return False, last, reason or "全部令牌均失败"


# ------------------------------------------------------------ 开关与记录
def enabled() -> bool:
    """通知是否开启（默认开启）：环境变量优先，其次本地配置 pr_notify。"""
    try:
        off = str(os.getenv("CODEVOYAGE_DISABLE_PR_NOTIFY") or "").strip().lower()
        if off in ("1", "true", "yes", "on"):
            return False
    except Exception:
        pass
    try:
        value = paths.load_local_conf().get("pr_notify")
    except Exception:
        value = None
    text = str(value or "").strip().lower()
    if not text:
        return True
    return text not in ("0", "false", "no", "off", "disable", "disabled")


def _file(repo_full: str) -> str:
    return os.path.join(paths.repo_dir(repo_full), "notify.json")


def load(repo_full: str) -> list:
    """读取某仓库已发送的通知记录（不存在或损坏时返回空列表）。"""
    items = paths.load_json(_file(repo_full), [])
    return items if isinstance(items, list) else []


def save(repo_full: str, items: list) -> None:
    try:
        os.makedirs(os.path.dirname(_file(repo_full)), exist_ok=True)
        paths.save_json(_file(repo_full), list(items)[:MAX_RECORDS])
    except Exception as e:
        _log(f"记录写入失败 {repo_full}: {e}")


# ------------------------------------------------------------ 回复（唯一写动作）
def reply(repo_full: str, ref, body: str) -> dict:
    """在 Issue / PR 下回复一条评论（令牌顺序回退）。

    这是本模块唯一的写动作：只留言，不改代码 / 分支 / Issue 状态。
    返回 {ok, url, reason}
    """
    repo_full = str(repo_full or "").strip()
    if not repo_full:
        return {"ok": False, "url": "", "reason": "未指定仓库"}
    if not str(ref or "").strip():
        return {"ok": False, "url": "", "reason": "未指定 Issue / PR 编号或链接"}
    if not str(body or "").strip():
        return {"ok": False, "url": "", "reason": "回复内容为空"}
    ok, data, reason = _with_tokens(
        repo_full, lambda t: _gh_issues().comment(t, repo_full, ref, body))
    if ok:
        return {"ok": True, "url": data.get("url", "") or "", "reason": ""}
    return {"ok": False, "url": "", "reason": reason or "回复失败"}


# ------------------------------------------------------------ 正文
def _quote(text: str) -> str:
    """把 AI 的结论摘录成引用块（逐行加 `>`，避免其内部标题打乱通知结构）。"""
    raw = sanitize.sanitize(text, CONCLUSION_LIMIT)
    if not raw:
        return ""
    lines = []
    for line in raw.splitlines():
        lines.append(f"> {line}".rstrip() if line.strip() else ">")
    return "\n".join(lines)


def build_body(task: dict, kind: str = "ok", pr_url: str = "", conclusion: str = "",
               error: str = "", info: dict | None = None) -> str:
    """生成通知正文（Markdown）。

    任务信息、结论、异常与轨迹文本一律先脱敏再写入；kind 未知时按 ok 处理。
    """
    task = task or {}
    info = info or {}
    kind = kind if kind in KINDS else "ok"
    repo_full = str(task.get("repo_full") or "").strip()
    number = task.get("issue_number")
    issue_url = str(task.get("issue_url") or "").strip()
    branch = sanitize.sanitize(str(info.get("branch") or "").strip(), 200)
    base = sanitize.sanitize(str(info.get("base") or "").strip(), 200)
    conflict = sanitize.sanitize(str(info.get("conflict") or "").strip(), 300)

    lines = [f"### CodeVoyage · {TITLES.get(kind, TITLES['ok'])}", ""]
    lines.append(f"- 仓库：`{repo_full or '未知'}`")
    if number:
        lines.append(f"- Issue：#{number}" + (f"（{issue_url}）" if issue_url else ""))
    if branch:
        lines.append(f"- 工作分支：`{branch}`" + (f" → `{base}`" if base else ""))
    if pr_url:
        lines.append(f"- Pull Request：{pr_url}")
    if conflict:
        lines.append(f"- PR 状态：{conflict}")
    lines.append(f"- 时间：{_now()}")

    if kind == "ok":
        lines += ["", "**结论摘要**", "",
                  _quote(conclusion) or "> （本次没有返回结论摘要，详见 PR 改动）"]
    elif kind == "failed":
        reason = sanitize.sanitize(error, 800)
        lines += ["", "**失败原因**", "", "```text", reason or "（未提供原因）", "```", "",
                  "修正后可在本 Issue 下再次评论触发词重试。"]
    elif kind == "putout":
        lines += ["", "用户在本机控制台选择了放弃，本次未对仓库做任何改动。"]
    else:
        lines += ["", sanitize.sanitize(str(info.get("note") or "") or "（无补充说明）", 800)]

    steps = info.get("steps") or []
    if steps:
        lines += ["", "<details><summary>自动处理步骤</summary>", ""]
        lines += [f"- {sanitize.sanitize(str(s), 300)}" for s in list(steps)[:STEP_LIMIT]]
        lines += ["", "</details>"]

    lines += [
        "", "---",
        "本回复由 CodeVoyage 自动发送（通知工作流）。执行细节见本机控制台的「任务 / 执行轨迹」。",
        "如需关闭自动回复：设置环境变量 `CODEVOYAGE_DISABLE_PR_NOTIFY=1`，"
        "或在本地配置中设置 `pr_notify=false`。",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------ 发送
def _remember(uid: str, kind: str, posted: list) -> None:
    """把已发送的通知登记到任务上（控制台任务列表可见），失败不影响流程。"""
    if not uid or not posted:
        return
    try:
        from centre import core

        first = posted[0] or {}
        core.note_notification(uid, kind, target=first.get("target", ""),
                               url=first.get("url", ""),
                               text=f"已自动回复通知（{kind}）")
    except Exception as e:
        _log(f"通知登记失败 {str(uid)[:8]}：{e}")


def notify(task: dict, kind: str = "ok", pr_url: str = "", conclusion: str = "",
           error: str = "", info: dict | None = None, force: bool = False,
           comment_pr: bool = True) -> dict:
    """发送任务结果通知（幂等；失败只返回原因，绝不抛异常）。

    - task     任务字典（需要 repo_full / issue_number / uuid / issue_url）；
    - kind     ok / failed / putout / manual；
    - pr_url   有 PR 时额外在 PR 下留一条同样的通知（comment_pr=False 可关闭）；
    - info     可选补充：branch / base / conflict（PR 状态说明）/ steps；
    - force    True 时忽略「已发送过」的记录，强制再发一次。

    返回 {ok, skipped, kind, repo, uuid, issue, pr, sent, reason, body}
    """
    task = task or {}
    kind = str(kind or "ok").strip().lower()
    if kind not in KINDS:
        kind = "ok"
    repo_full = str(task.get("repo_full") or "").strip()
    uid = str(task.get("uuid") or "").strip()
    number = task.get("issue_number")
    result = {"ok": False, "skipped": False, "kind": kind, "repo": repo_full, "uuid": uid,
              "issue": number, "pr": pr_url, "sent": [], "reason": "", "body": ""}

    if not repo_full:
        result.update({"skipped": True, "reason": "任务缺少仓库信息，跳过通知"})
        return result
    if not enabled():
        result.update({"skipped": True,
                       "reason": "通知已关闭（CODEVOYAGE_DISABLE_PR_NOTIFY=1 或 pr_notify=false），跳过通知"})
        _log(f"{repo_full} 通知已关闭，跳过 {kind} 通知")
        return result

    with _lock:
        records = load(repo_full)
        if uid and not force:
            for rec in records:
                if rec.get("uuid") == uid and rec.get("kind") == kind:
                    result.update({
                        "ok": True, "skipped": True,
                        "sent": rec.get("sent") or [],
                        "reason": f"同一任务已发送过 {kind} 通知（{rec.get('at', '')}），未重复发送",
                    })
                    return result
        if not paths.resolve_tokens(repo_full):
            result["reason"] = "该仓库没有可用令牌，无法发送通知"
            return result

        body = build_body(task, kind=kind, pr_url=pr_url, conclusion=conclusion,
                          error=error, info=info)
        result["body"] = body

        targets = []
        if number:
            targets.append(("issue", number))
        if pr_url and comment_pr:
            targets.append(("pr", pr_url))
        if not targets:
            result.update({"skipped": True, "reason": "任务没有 Issue / PR 可回复，跳过通知"})
            return result

        for name, ref in targets:
            res = reply(repo_full, ref, body)
            entry = {"target": name, "url": res.get("url") or "",
                     "ok": bool(res.get("ok")),
                     "reason": "" if res.get("ok") else (res.get("reason") or "回复失败")}
            result["sent"].append(entry)
            if not entry["ok"]:
                _log(f"{repo_full} #{number} 在 {TARGET_NAMES.get(name, name)} 回复失败：{entry['reason']}")

        posted = [e for e in result["sent"] if e["ok"]]
        if not posted:
            result["reason"] = "通知发送失败：" + "；".join(
                f"{TARGET_NAMES.get(e['target'], e['target'])} {e['reason']}" for e in result["sent"])
            return result

        result["ok"] = True
        result["reason"] = "已自动回复 " + "、".join(
            TARGET_NAMES.get(e["target"], e["target"]) for e in posted)
        records.insert(0, {"repo_full": repo_full, "uuid": uid, "kind": kind, "issue": number,
                           "pr": pr_url, "sent": result["sent"], "at": _now()})
        save(repo_full, records)
        _remember(uid, kind, posted)
        _log(f"{repo_full} #{number} {result['reason']}（{kind}）")
    return result


def format_result(result: dict) -> str:
    """把 notify 的返回值整理成给 AI / 人看的文本（写入轨迹与日志）。"""
    result = result or {}
    lines = []
    if result.get("skipped"):
        lines.append(result.get("reason") or "已跳过通知")
    elif result.get("ok"):
        lines.append(f"{result.get('reason') or '已发送通知'}；内容长度 {len(result.get('body') or '')} 字")
    else:
        lines.append(f"通知未发送：{result.get('reason') or '未知原因'}")
    for item in result.get("sent") or []:
        name = TARGET_NAMES.get(item.get("target"), item.get("target"))
        lines.append(f"  - {name}：{item.get('url') or item.get('reason')}")
    return "\n".join(lines) if lines else "无通知"
