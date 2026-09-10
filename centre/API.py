"""本地中心 5431 的 API 路由。

分三类：
1. 账号类：/api/user/*（远端为主，本模块代理登录并把凭证存 ~/.CodeVoyage/user/conf.json）
2. 远端数据代理：/api/repo/*、/api/records（自动附带 ID+token 转发到远端）
3. 本地专属：/api/GetIssue（GetIssue 组件上报入库）、/api/Agent/*（队列/状态/确认/日志）、
   /api/local/config（GitHub PAT、LLM Key 等本地配置）
"""
import re

from flask import jsonify, request

from centre import core, paths, remote


def register(app):
    # ---------------------------------------------------------------
    # 用户：登录 / 注册 / 登出 / 状态
    # ---------------------------------------------------------------
    @app.route("/api/user/login", methods=["POST"])
    def user_login():
        data = request.get_json(silent=True) or {}
        try:
            conf = remote.login((data.get("email") or "").strip(), data.get("password") or "")
        except remote.RemoteError as e:
            return jsonify({"message": e.message}), e.code
        except Exception as e:
            return jsonify({"message": f"无法连接远端服务：{e}"}), 502
        return jsonify({"message": "OK", "email": conf["email"], "ID": conf["ID"]})

    @app.route("/api/user/register", methods=["POST"])
    def user_register():
        data = request.get_json(silent=True) or {}
        try:
            conf = remote.register((data.get("email") or "").strip(), data.get("password") or "")
        except remote.RemoteError as e:
            return jsonify({"message": e.message}), e.code
        except Exception as e:
            return jsonify({"message": f"无法连接远端服务：{e}"}), 502
        return jsonify({"message": "OK", "email": conf["email"], "ID": conf["ID"]})

    @app.route("/api/user/logout", methods=["POST"])
    def user_logout():
        paths.clear_user_conf()
        return jsonify({"message": "OK"})

    @app.route("/api/user/status", methods=["GET", "POST"])
    def user_status():
        conf = paths.load_user_conf()
        local = paths.load_local_conf()
        return jsonify({
            "message": "OK",
            "logged_in": bool(conf),
            "email": (conf or {}).get("email", ""),
            "remote": paths.remote_base(),
            "github_configured": bool(local.get("github_token")),
            "llm_configured": bool(local.get("llm_api_key")),
            "llm_model": local.get("llm_model") or "",
        })

    @app.route("/api/remote/set", methods=["POST"])
    def remote_set():
        data = request.get_json(silent=True) or {}
        url = (data.get("remote") or "").strip()
        if not url.startswith(("http://", "https://")):
            return jsonify({"message": "remote 地址需以 http(s):// 开头"}), 400
        paths.set_remote_base(url)
        return jsonify({"message": "OK", "remote": paths.remote_base()})

    # ---------------------------------------------------------------
    # 远端数据代理
    # ---------------------------------------------------------------
    def _proxy_raw(endpoint):
        try:
            resp = remote.call(endpoint, request.get_json(silent=True) or {}, raw=True)
        except Exception as e:
            return jsonify({"message": f"远端请求失败：{e}"}), 502
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype or endpoint not in ("/api/repo/workflow",):
            try:
                return jsonify(resp.json()), resp.status_code
            except ValueError:
                return jsonify({"message": resp.text or "unknown"}), resp.status_code
        from flask import Response

        return Response(resp.content, status=resp.status_code, mimetype="text/plain",
                        headers={"Content-Disposition": resp.headers.get("Content-Disposition", "")})

    for endpoint in ("/api/repo/list", "/api/repo/bind", "/api/repo/update",
                     "/api/repo/unbind", "/api/repo/workflow", "/api/records", "/api/user/info"):
        def make_view(ep=endpoint):
            def view():
                return _proxy_raw(ep)
            return view
        app.add_url_rule(endpoint, endpoint=f"proxy_{endpoint.strip('/').replace('/', '_')}",
                         view_func=make_view(), methods=["POST"])

    # ---------------------------------------------------------------
    # 仓库检查器：workflow 是否已安装（本地 GitHub PAT 调用，Key 不出本机）
    # ---------------------------------------------------------------
    @app.route("/api/repo/check-workflow", methods=["POST"])
    def check_workflow():
        data = request.get_json(silent=True) or {}
        owner = (data.get("owner") or "").strip()
        name = (data.get("name") or "").strip()
        if not owner or not name:
            return jsonify({"message": "owner or name missing"}), 400
        token = (paths.load_local_conf().get("github_token") or "").strip()
        if not token:
            return jsonify({
                "message": "OK", "installed": False, "up_to_date": False, "file": "",
                "files": [], "reason": "未配置 GitHub Token（本地配置），无法检查仓库",
            })
        try:
            from GithubTool import user as gh_user

            res = gh_user.list_workflows(token, f"{owner}/{name}")
        except Exception as e:
            return jsonify({
                "message": "OK", "installed": False, "up_to_date": False, "file": "",
                "files": [], "reason": f"检查失败：{e}",
            })
        files = res.get("files", [])
        matched = next((f for f in files if f.get("is_codevoyage")), None)
        return jsonify({
            "message": "OK",
            "installed": bool(matched),
            "up_to_date": bool(matched and matched.get("has_author_check")),
            "file": matched["name"] if matched else "",
            "files": files,
            "reason": res.get("reason", ""),
        })

    # ---------------------------------------------------------------
    # 一键安装：本地提交 workflow 文件并发起 PR（PAT 不出本机）
    # ---------------------------------------------------------------
    @app.route("/api/repo/install-workflow", methods=["POST"])
    def install_workflow():
        data = request.get_json(silent=True) or {}
        owner = (data.get("owner") or "").strip()
        name = (data.get("name") or "").strip()
        bind_id = data.get("id")
        if not owner or not name or not bind_id:
            return jsonify({"message": "owner/name/id missing"}), 400
        token = (paths.load_local_conf().get("github_token") or "").strip()
        if not token:
            return jsonify({"message": "OK", "ok": False,
                            "reason": "未配置 GitHub Token（本地配置），无法提交 PR"})
        # 1) 取远端生成的 workflow 文本（以绑定的关键词/白名单为准）
        try:
            resp = remote.call("/api/repo/workflow", {"id": bind_id}, raw=True)
        except Exception as e:
            return jsonify({"message": "OK", "ok": False, "reason": f"获取 workflow 失败：{e}"})
        if resp.status_code >= 400:
            return jsonify({"message": "OK", "ok": False,
                            "reason": f"获取 workflow 失败（{resp.status_code}）：{(resp.text or '')[:200]}"})
        content = resp.text
        filename = f"codevoyage-{owner}-{name}.yml"
        m = re.search(r'filename="?([^";]+)"?', resp.headers.get("Content-Disposition") or "")
        if m:
            filename = m.group(1)
        path = f".github/workflows/{filename}"
        # 2) 建分支提交并发起 PR
        try:
            from GithubTool import user as gh_user

            res = gh_user.commit_workflow_pr(token, f"{owner}/{name}", path, content)
        except Exception as e:
            return jsonify({"message": "OK", "ok": False, "reason": f"创建 PR 失败：{e}"})
        return jsonify({
            "message": "OK", "ok": True, "pr_url": res["pr_url"], "branch": res["branch"],
            "filename": filename, "path": path, "existed": res["existed"],
        })

    # ---------------------------------------------------------------
    # 本地配置（GitHub Token / LLM）
    # ---------------------------------------------------------------
    @app.route("/api/local/config", methods=["POST"])
    def local_config_save():
        data = request.get_json(silent=True) or {}
        allowed = {"github_token", "llm_api_key", "llm_base_url", "llm_model"}
        update = {k: str(v) for k, v in data.items() if k in allowed}
        current = paths.load_local_conf()
        current.update(update)
        paths.save_local_conf(current)
        return jsonify({
            "message": "OK",
            "github_configured": bool(current.get("github_token")),
            "llm_configured": bool(current.get("llm_api_key")),
            "llm_model": current.get("llm_model") or "",
        })

    # ---------------------------------------------------------------
    # GetIssue 组件上报（远端 -> GetIssue 组件 -> 5431 入库）
    # ---------------------------------------------------------------
    @app.route("/api/GetIssue", methods=["POST"])
    def receive_issue():
        data = request.get_json(silent=True) or {}
        issue = data.get("Issue")
        if not issue or not issue.get("repo_full"):
            return jsonify({"message": "invalid issue payload"}), 400
        task = core.enqueue_issue(issue)
        return jsonify({"message": "OK", "uuid": task.get("uuid")})

    # ---------------------------------------------------------------
    # Agent 可视化：任务列表 / 状态 / 确认 / 日志
    # ---------------------------------------------------------------
    @app.route("/api/Agent/task/decision", methods=["POST"])
    def agent_decision():
        data = request.get_json(silent=True) or {}
        uid = data.get("uuid") or ""
        decision = data.get("decision")
        yes = decision in (True, "yes", "true", "1", 1)
        no = decision in (False, "no", "false", "0", 0)
        if not uid or not (yes or no):
            return jsonify({"message": "uuid or decision invalid"}), 400
        if not core.decide(uid, yes):
            return jsonify({"message": "task not found"}), 404
        return jsonify({"message": "OK"})

    @app.route("/api/Agent/tasks", methods=["GET", "POST"])
    def agent_tasks():
        state = None
        if request.method == "POST":
            state = (request.get_json(silent=True) or {}).get("state")
        else:
            state = request.args.get("state")
        tasks = core.tasks_snapshot()
        if state:
            tasks = [t for t in tasks if t.get("state") == state]
        for t in tasks:
            for k in ("body", "comments"):
                if k in t and isinstance(t[k], (dict, list)):
                    import json as _json

                    t[k] = _json.dumps(t[k], ensure_ascii=False)[:500]
        return jsonify({"message": "OK", "tasks": tasks})

    @app.route("/api/Agent/overview", methods=["GET"])
    def agent_overview():
        conf = paths.load_user_conf()
        snapshot = core.tasks_snapshot(60)
        confirming = [t for t in snapshot if t.get("state") == "confirming" and t.get("decided") is None]
        running = next((t for t in snapshot if t.get("state") == "running"), None)
        return jsonify({
            "message": "OK",
            "logged_in": bool(conf),
            "email": (conf or {}).get("email", ""),
            "state": core.get_state(),
            "confirming": confirming,
            "running": running,
            "tasks": snapshot,
            "logs": paths.tail_log(200),
        })

    @app.route("/api/Agent/state", methods=["GET"])
    def agent_state():
        return jsonify({"message": "OK", "state": core.get_state()})

    @app.route("/api/Agent/logs", methods=["GET"])
    def agent_logs():
        return jsonify({"message": "OK", "logs": paths.tail_log(500)})
