"""Agent 引擎。

一次任务流程：
1. 克隆仓库到临时工作区，创建分支；
2. 组装系统提示（含工作区边界）与 Issue 内容，调用 LLM（多条 LLM 配置按顺序回退）；
3. LLM 通过工具（read_file/write_file/list_dir/plan）在工作区内完成修改；
4. 全过程写入执行轨迹（centre.trace），控制台可实时查看与事后回看；
5. 解析最终答复中的 Conclusion，作为提交信息；
6. git 提交并推送分支，调用 GitHub API 创建 Pull Request；
7. 登记本次工作分支（centre.branch_gc）：PR 合并进默认分支后由后台巡检自动销毁，
   保证项目仓库的分支管理规范可维护（Issue #13）；
8. 返回结果由上层回执给远端。

工作区安全：work 目录名由远端下发的任务 uuid 拼成，注入文件工具之前必须
（1）用 `paths.safe_name` 清洗 uuid；（2）校验最终路径位于 `paths.REPO_DIR` 之内；
（3）`ReadFile.set_workspace(dest, root=paths.REPO_DIR)` 再做一次根目录约束。
这样即使远端下发的 uuid 里带 `..` / 分隔符，也无法把 AI 的文件工具指到工作区之外。

凭据来源：GitHub PAT / LLM Key 存于服务端（加密），本机只保留同步缓存（centre.credentials）；
提交身份仍取本机配置。
"""
import json
import os

import config
import Info
from centre import branch_gc, core, credentials, paths, proxy, trace

_MAX_ITERATIONS = 80


class AgentError(Exception):
    pass


def _validate_workspace(dest: str) -> str:
    """校验工作区目录必须落在 CodeVoyage 数据目录的 Agent/repo 之下，返回 realpath。"""
    base = os.path.normcase(os.path.realpath(os.path.abspath(paths.REPO_DIR)))
    target = os.path.normcase(os.path.realpath(os.path.abspath(dest)))
    if target != base and not target.startswith(base + os.sep):
        raise AgentError(f"工作区路径越界，已拒绝：{dest}")
    return target


def _llm_candidates() -> list:
    """按顺序返回可用的 LLM 配置（存服务端，多条并存，前者失败自动回退）。"""
    out = []
    for c in paths.load_llm_configs():
        api_key = (c.get("api_key") or "").strip()
        if not api_key:
            continue
        model = (c.get("model") or "").strip() or Info.Agent.model
        out.append({
            "id": c.get("id"),
            "name": (c.get("name") or "").strip() or model,
            "api_key": api_key,
            "base_url": (c.get("base_url") or Info.Agent.base_url).strip() or Info.Agent.base_url,
            "model": model,
        })
    return out


def _save_history(repo_full: str, messages: list) -> None:
    try:
        paths.save_json(paths.history_file(repo_full), {"messages": messages[-200:]})
    except Exception:
        pass


def _exec_tool(name: str, arguments: str) -> str:
    """按 Info.Agent.tool_impl 分发表执行工具，返回值直接给模型。"""
    impl_path = Info.Agent.tool_impl.get(name)
    if not impl_path:
        return f"Error: unknown tool `{name}`"
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    try:
        mod_name, func_name = impl_path.split(".")
        mod = getattr(__import__("tools", fromlist=[mod_name]), mod_name)
        result = getattr(mod, func_name)(**args)
        return str(result)
    except Exception as e:
        return f"Error: tool `{name}` 执行失败: {e}"


def _check_tool(result) -> None:
    """兜底调用工具时，返回以「错误：/Error:」开头表示失败，直接抛出避免假成功。"""
    text = str(result or "")
    if text.startswith(("错误：", "错误:", "Error:", "Error：")):
        raise AgentError(text)


def _build_issue_prompt(task: dict) -> str:
    lines = [
        f"仓库：{task['repo_full']}",
        f"Issue #{task.get('issue_number')}：{task.get('title') or '(无标题)'}",
        f"Issue 链接：{task.get('issue_url') or ''}",
        "",
        "Issue 内容：",
        str(task.get("body_text") or task.get("body") or "(空)"),
    ]
    comments = task.get("comments") or []
    if comments:
        lines.append("")
        lines.append("已有评论：")
        for i, c in enumerate(comments[:50], 1):
            lines.append(f"{i}. {c.get('user', '?')}: {c.get('body', '')}")
    lines.append("")
    lines.append("请在修改前先调用 plan 说明处理计划，随后使用工具读取与修改工作区代码。")
    lines.append("最终答复必须是完整合法 Markdown，并包含 Introduce / Body / Conclusion 三部分；")
    lines.append("Conclusion 用于生成提交与 PR 信息，请在其中总结你做的每一处更改与原因。")
    return "\n".join(lines)


def run_task(task: dict) -> dict:
    """执行单个 Issue 任务，返回 {status, pr_url, conclusion}。异常以 AgentError 抛出。"""
    repo_full = task["repo_full"]
    uid = task.get("uuid", "")
    issue_number = task.get("issue_number")

    # 令牌存服务端，先尽力同步一次（远端不可用时沿用本机缓存）
    try:
        credentials.sync()
    except Exception:
        pass
    # 候选令牌（顺序即尝试顺序）：仓库专属（细粒度）→ 全局（传统）
    token_candidates = paths.resolve_tokens(repo_full)
    if not token_candidates:
        raise AgentError(
            f"仓库 {repo_full} 没有可用令牌：请在「配置」页为该仓库添加细粒度令牌，"
            "或添加全局传统令牌"
        )
    llm_candidates = _llm_candidates()
    if not llm_candidates:
        raise AgentError("未配置 LLM API Key，请在控制台「配置」页添加")

    # uuid 来自远端：先清洗成安全的单个路径片段，再拼工作区目录
    dest = _validate_workspace(os.path.join(paths.work_dir(repo_full), paths.safe_name(uid, "run")))

    trace.start(repo_full, task)
    core.set_activity({"running_uuid": uid, "repo_full": repo_full,
                       "status": "running", "note": "AI 操作仓库中"})
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    from tools import GitRepo, ReadFile, RepoOps

    # 克隆 / 建分支 / 提交推送 / 建 PR 全部由 AI 调用工具完成；
    # 这里只注入上下文并先划定工作区边界（文件工具一律限制在此目录内）。
    RepoOps.set_context(repo_full, uid, issue_number, dest)
    try:
        # root 约束：只允许把文件工具的工作区设在 Agent/repo 目录之下
        ReadFile.set_workspace(dest, root=paths.REPO_DIR)
    except ValueError as e:
        raise AgentError(f"工作区初始化失败：{e}") from e

    try:

        # ---------------- 组装提示（带上一轮会话摘要，实现会话复用） ----------------
        work_abs = os.path.abspath(dest)
        system = config.AgentSystem.replace("你的工作区在 xxx", f"你的工作区在 {work_abs}")
        messages = [{"role": "system", "content": system}]
        recap = _session_recap(paths.load_json(paths.history_file(repo_full), {}) or {})
        if recap:
            messages.append({"role": "assistant", "content": recap})
            trace.append(repo_full, uid, {"type": "session", "content": recap})
            paths.append_log(f"[{repo_full}#{issue_number}] 复用上一轮会话摘要（{len(recap)} 字）")
        messages.append({"role": "user", "content": _build_issue_prompt(task)})

        final_content = _run_llm_loop(repo_full, uid, messages, llm_candidates, issue_number)

        # ---------------- 收尾：AI 若漏了最后几步，程序兜底，保证改动不丢 ----------------
        conclusion = _extract_conclusion(final_content)
        pr_title = f"{task.get('title') or 'AI fix'} (#{issue_number})"[:100]

        if not RepoOps.state()["cloned"]:
            raise AgentError("AI 未调用 clone_repo 克隆仓库，任务未完成")
        if not RepoOps.state()["branch"]:
            trace.append(repo_full, uid, {"type": "note", "content": "AI 未创建分支，程序兜底创建"})
            _check_tool(RepoOps.create_branch())
        if not RepoOps.state()["pushed"]:
            if not GitRepo.changed_files(dest).strip():
                raise AgentError("AI 未产生任何文件改动")
            trace.append(repo_full, uid, {"type": "note", "content": "AI 未提交，程序兜底提交并推送"})
            core.set_activity({"note": "兜底提交并推送"})
            _check_tool(RepoOps.commit_and_push(conclusion or final_content[:500] or pr_title))
        if not RepoOps.state()["pr_url"]:
            trace.append(repo_full, uid, {"type": "note", "content": "AI 未建 PR，程序兜底创建"})
            _check_tool(RepoOps.create_pull_request(pr_title, final_content))

        pr_url = RepoOps.state()["pr_url"]
        if not pr_url:
            raise AgentError("未获得 PR 链接，任务未真正完成")

        # 登记本次工作分支：PR 合并进默认分支后由后台巡检自动销毁（Issue #13）。
        # 只登记不删除，避免 PR 尚未合并时误删工作分支。
        branch_gc.register(repo_full, RepoOps.state().get("branch", ""), pr_url)

        paths.append_log(f"[{repo_full}#{issue_number}] 已完成，PR：{pr_url}")
        trace.finish(repo_full, uid, "ok")
        return {"status": "ok", "pr_url": pr_url, "conclusion": conclusion}
    except Exception as e:
        trace.append(repo_full, uid, {"type": "error", "content": str(e)})
        trace.finish(repo_full, uid, "failed", str(e))
        raise
    finally:
        # 成功/失败都清理临时克隆，避免磁盘膨胀
        GitRepo.remove_dir(dest)
        ReadFile.set_workspace("")


def _session_recap(history: dict) -> str:
    """从上一次会话里取最后一段 AI 结论，作为本轮参考（会话复用）。"""
    messages = (history or {}).get("messages") or []
    answers = [
        str(m.get("content") or "").strip()
        for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and str(m.get("content") or "").strip()
    ]
    if not answers:
        return ""
    last = answers[-1]
    if len(last) > 1500:
        last = last[:1500] + "…"
    return "上一轮任务的处理结论（供本次参考，可直接复用相关判断）：\n" + last


def _run_llm_loop(repo_full: str, uid: str, messages: list, llm_candidates: list, issue_number) -> str:
    """工具调用循环：把思考 / 工具调用 / 结果 / 最终回答写入执行轨迹；LLM 配置按顺序回退。"""
    from openai import OpenAI

    preferred = 0
    iteration = 0
    while iteration < _MAX_ITERATIONS:
        iteration += 1
        core.set_activity({"note": f"调用 AI（第 {iteration} 轮）"})
        response = None
        last_error = None
        order = list(range(len(llm_candidates)))
        order = order[preferred:] + order[:preferred]
        for idx in order:
            cfg = llm_candidates[idx]
            client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
            try:
                response = client.chat.completions.create(
                    model=cfg["model"],
                    messages=messages,
                    tools=Info.Agent.tools,
                    tool_choice="auto",
                    stream=False,
                )
                preferred = idx
                break
            except Exception as e:
                last_error = e
                paths.append_log(f"[{repo_full}#{issue_number}] LLM 配置「{cfg['name']}」调用失败：{e}")
                trace.append(repo_full, uid, {
                    "type": "error", "iteration": iteration,
                    "content": f"LLM 配置「{cfg['name']}」调用失败：{e}",
                })
        if response is None:
            raise AgentError(f"全部 {len(llm_candidates)} 个 LLM 配置都调用失败：{last_error}")

        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        if finish_reason == "tool_calls" and msg.tool_calls:
            thinking = (msg.content or "").strip()
            if thinking:
                trace.append(repo_full, uid, {"type": "thinking", "iteration": iteration,
                                              "content": thinking})
            messages.append(msg.model_dump(exclude_none=True))
            for tc in msg.tool_calls:
                trace.append(repo_full, uid, {"type": "tool_call", "iteration": iteration,
                                              "tool": tc.function.name,
                                              "content": tc.function.arguments or ""})
                result = _exec_tool(tc.function.name, tc.function.arguments)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                trace.append(repo_full, uid, {"type": "tool_result", "iteration": iteration,
                                              "tool": tc.function.name,
                                              "content": str(result)[:2000]})
            _save_history(repo_full, messages)
            continue
        final_content = (msg.content or "").strip()
        messages.append({"role": "assistant", "content": final_content})
        _save_history(repo_full, messages)
        if not final_content:
            raise AgentError("AI 未返回任何内容")
        trace.append(repo_full, uid, {"type": "answer", "iteration": iteration,
                                      "content": final_content})
        return final_content
    raise AgentError(f"AI 工具循环超过 {_MAX_ITERATIONS} 轮仍未结束")


def _extract_conclusion(markdown: str) -> str:
    """提取 AI 最终答复中的 Conclusion 段落；找不到则原样返回。"""
    if not markdown:
        return ""
    import re

    for pat in (r"#{1,6}\s*Conclusion\b", r"\*\*Conclusion\*\*", r"结论\s*[:：]"):
        m = re.search(pat, markdown, flags=re.IGNORECASE)
        if m:
            rest = markdown[m.end():].strip()
            # 截到下一个标题（若有）
            nxt = re.search(r"\n#{1,6}\s+\S", rest)
            if nxt:
                rest = rest[: nxt.start()]
            return rest.strip()[:2000]
    return markdown[:500].strip()


# ------------------------------ 兼容旧入口（仅调试） ------------------------------
def call_ai(message: str = "") -> str:
    candidates = _llm_candidates()
    if not candidates:
        return "未配置 LLM API Key"
    cfg = candidates[0]
    from openai import OpenAI

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    messages = [{"role": "user", "content": message or "你好"}]
    try:
        resp = client.chat.completions.create(
            model=cfg["model"], messages=messages, tools=Info.Agent.tools, tool_choice="auto", stream=False
        )
    except Exception as e:
        return f"调用失败：{e}"
    return (resp.choices[0].message.content or "").strip()
