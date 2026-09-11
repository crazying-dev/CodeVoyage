"""Agent 引擎。

一次任务流程：
1. 克隆仓库到临时工作区，创建分支；
2. 组装系统提示（含工作区边界）与 Issue 内容，调用 LLM；
3. LLM 通过工具（read_file/write_file/list_dir/plan）在工作区内完成修改；
4. 解析最终答复中的 Conclusion，作为提交信息；
5. git 提交并推送分支，调用 GitHub API 创建 Pull Request；
6. 返回结果由上层回执给远端。

LLM Key 与 GitHub PAT 均从本地配置读取（~/.CodeVoyage/local.json，混淆落盘），绝不外传。
"""
import json
import os

import config
import Info
from centre import core, paths

_MAX_ITERATIONS = 80


class AgentError(Exception):
    pass


def _client():
    local = paths.load_local_conf()
    key = (local.get("llm_api_key") or "").strip()
    if not key:
        raise AgentError("未配置 LLM API Key，请在控制台「本地配置」中填写")
    from openai import OpenAI

    base = (local.get("llm_base_url") or Info.Agent.base_url).strip() or Info.Agent.base_url
    return OpenAI(api_key=key, base_url=base)


def _model(local: dict) -> str:
    return (local.get("llm_model") or Info.Agent.model).strip() or Info.Agent.model


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
    local = paths.load_local_conf()
    repo_full = task["repo_full"]
    # 候选令牌（顺序即尝试顺序）：仓库专属（细粒度）→ 全局（传统）
    token_candidates = paths.resolve_tokens(repo_full)
    if not token_candidates:
        raise AgentError(
            f"仓库 {repo_full} 没有可用令牌：请在「仓库绑定」页填写该仓库的细粒度令牌，"
            "或在「本地配置」页填写全局传统令牌"
        )
    if not (local.get("llm_api_key") or "").strip():
        raise AgentError("未配置 LLM API Key，请在控制台「本地配置」中填写")

    issue_number = task.get("issue_number")
    branch = f"codevoyage/issue-{issue_number}-{task.get('uuid', '')[:6]}"
    dest = os.path.join(paths.repo_dir(repo_full), "work", task.get("uuid", "run"))

    core.set_activity({"running_uuid": task.get("uuid", ""), "status": "running", "note": "克隆仓库"})
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    from tools import GitRepo, ReadFile

    try:
        # 依次尝试各令牌克隆，第一个成功即采用
        gh_token, token_source, clone_error = "", "", None
        for idx, (cand_token, cand_source) in enumerate(token_candidates, start=1):
            GitRepo.remove_dir(dest)
            try:
                GitRepo.clone(repo_full, cand_token, dest)
                gh_token, token_source = cand_token, cand_source
                paths.append_log(
                    f"[{repo_full}#{issue_number}] 克隆成功（第 {idx} 个令牌，来源 "
                    f"{'仓库专属/细粒度' if cand_source == 'repo' else '全局/传统'}）"
                )
                break
            except Exception as e:
                clone_error = e
                paths.append_log(f"[{repo_full}#{issue_number}] 第 {idx} 个令牌克隆失败：{e}")
        if not gh_token:
            hint = ""
            low = str(clone_error).lower()
            if any(k in low for k in ("connect", "timed out", "timeout", "resolve host", "unable to access", "network")):
                hint = "（网络类失败，已按 5 次 / 5 秒重试；如网络受限可设置环境变量 CODEVOYAGE_PROXY）"
            raise AgentError(f"全部 {len(token_candidates)} 个令牌都无法克隆仓库：{clone_error}{hint}")

        default = ""
        try:
            from GithubTool import user as gh_user

            default = gh_user.default_branch(gh_token, repo_full)
        except Exception:
            default = GitRepo.current_branch(dest)
        if not default:
            default = GitRepo.current_branch(dest)

        GitRepo.create_branch(dest, branch)
        ReadFile.set_workspace(dest)

        # ---------------- 组装并调用 LLM ----------------
        work_abs = os.path.abspath(dest)
        system = config.AgentSystem.replace("你的工作区在 xxx", f"你的工作区在 {work_abs}")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _build_issue_prompt(task)},
        ]
        _save_history(repo_full, messages)

        client = _client()
        model = _model(local)
        final_content = ""
        iteration = 0
        while iteration < _MAX_ITERATIONS:
            iteration += 1
            core.set_activity({"note": f"调用 AI（第 {iteration} 轮）"})
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=Info.Agent.tools,
                    tool_choice="auto",
                    stream=False,
                )
            except Exception as e:
                raise AgentError(f"AI 调用失败：{e}")

            msg = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "tool_calls" and msg.tool_calls:
                dumped = msg.model_dump(exclude_none=True)
                messages.append(dumped)
                for tc in msg.tool_calls:
                    result = _exec_tool(tc.function.name, tc.function.arguments)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                _save_history(repo_full, messages)
                continue
            final_content = (msg.content or "").strip()
            messages.append({"role": "assistant", "content": final_content})
            _save_history(repo_full, messages)
            if not final_content:
                raise AgentError("AI 未返回任何内容")
            break
        else:
            raise AgentError(f"AI 工具循环超过 {_MAX_ITERATIONS} 轮仍未结束")

        # ---------------- 提交、推送、建 PR ----------------
        conclusion = _extract_conclusion(final_content)
        commit_message = conclusion or final_content[:500] or f"AI fix: {task.get('title') or ''} (#{issue_number})"
        pr_title = f"{task.get('title') or 'AI fix'} (#{issue_number})"[:100]
        pr_body = final_content

        core.set_activity({"note": "git 提交并推送"})
        git_name, git_email = paths.git_identity()
        GitRepo.commit_all(dest, commit_message, name=git_name, email=git_email)
        GitRepo.push(dest, repo_full, gh_token, branch)

        from GithubTool import user as gh_user

        pr_url = gh_user.create_pr(gh_token, repo_full, branch, default, pr_title, pr_body)
        paths.append_log(f"[{repo_full}#{issue_number}] 已创建 PR: {pr_url}")
        return {"status": "ok", "pr_url": pr_url, "conclusion": conclusion}
    finally:
        # 成功/失败都清理临时克隆，避免磁盘膨胀
        GitRepo.remove_dir(dest)
        ReadFile.set_workspace("")


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
    local = paths.load_local_conf()
    client = _client()
    model = _model(local)
    messages = [{"role": "user", "content": message or "你好"}]
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=Info.Agent.tools, tool_choice="auto", stream=False
        )
    except Exception as e:
        return f"调用失败：{e}"
    return (resp.choices[0].message.content or "").strip()
