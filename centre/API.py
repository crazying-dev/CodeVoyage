"""本地中心 5431 的 API 路由。

分三类：
1. 账号类：/api/user/*（远端为主，本模块代理登录并把凭证存 ~/.CodeVoyage/user/conf.json）
2. 远端数据代理：/api/repo/*、/api/records（自动附带 ID+token 转发到远端）
3. 本地专属：/api/GetIssue（GetIssue 组件上报入库）、/api/Agent/*（队列/状态/确认/日志）、
   /api/local/config（GitHub PAT、LLM Key 等本地配置）
"""
import re
import threading

from flask import jsonify, request

from centre import chat, core, credentials, net, paths, proxy, remote, trace


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
        except paths.StorageError as e:
            return jsonify({"message": str(e)}), 500
        except Exception as e:
            return jsonify({"message": f"无法连接远端服务：{e}"}), 502
        threading.Thread(target=credentials.startup, daemon=True).start()
        return jsonify({"message": "OK", "email": conf["email"], "ID": conf["ID"]})

    @app.route("/api/user/register", methods=["POST"])
    def user_register():
        data = request.get_json(silent=True) or {}
        try:
            conf = remote.register((data.get("email") or "").strip(), data.get("password") or "")
        except remote.RemoteError as e:
            return jsonify({"message": e.message}), e.code
        except paths.StorageError as e:
            return jsonify({"message": str(e)}), 500
        except Exception as e:
            return jsonify({"message": f"无法连接远端服务：{e}"}), 502
        threading.Thread(target=credentials.startup, daemon=True).start()
        return jsonify({"message": "OK", "email": conf["email"], "ID": conf["ID"]})

    @app.route("/api/user/logout", methods=["POST"])
    def user_logout():
        paths.clear_user_conf()
        # 凭据缓存在本机，退出登录必须一并清掉，避免下一个账号复用
        credentials.invalidate()
        try:
            paths.save_cred_cache({})
        except Exception:
            pass
        return jsonify({"message": "OK"})

    @app.route("/api/user/status", methods=["GET", "POST"])
    def user_status():
        conf = paths.load_user_conf()
        global_tokens = paths.load_global_tokens()
        llm_configs = paths.load_llm_configs()
        first_llm = llm_configs[0] if llm_configs else {}
        return jsonify({
            "message": "OK",
            "logged_in": bool(conf),
            "email": (conf or {}).get("email", ""),
            "remote": paths.remote_base(),
            "storage_dir": paths.BASE_DIR,
            "storage_source": paths.BASE_SOURCE,
            "github_configured": bool(global_tokens),
            "github_tokens": paths.token_hint_list(global_tokens),
            "github_token_count": len(global_tokens),
            "github_token_hint": paths.mask(global_tokens[0]) if global_tokens else "",
            "llm_configured": bool(llm_configs),
            "llm_count": len(llm_configs),
            "llm_api_key_hint": paths.mask(first_llm.get("api_key", "")),
            "llm_base_url": first_llm.get("base_url", ""),
            "llm_model": first_llm.get("model", ""),
            "git_name": paths.git_identity()[0],
            "git_email": paths.git_identity()[1],
        })

    # ---------------------------------------------------------------
    # 本地诊断：存储路径/可写性/文件落地情况 + 登录态有效性
    # ---------------------------------------------------------------
    @app.route("/api/local/diagnostics", methods=["GET", "POST"])
    def local_diagnostics():
        conf = paths.load_user_conf()
        session = {"checked": False, "valid": False, "reason": ""}
        if conf:
            try:
                remote.call("/api/user/info", {}, timeout=15)
                session = {"checked": True, "valid": True, "reason": ""}
            except remote.RemoteError as e:
                session = {"checked": True, "valid": False, "reason": f"远端拒绝（{e.code}）：{e.message}"}
            except Exception as e:
                session = {"checked": False, "valid": False, "reason": f"无法连接远端：{e}"}
        return jsonify({
            "message": "OK",
            "remote": paths.remote_base(),
            "logged_in": bool(conf),
            "session": session,
            "storage": paths.storage_info(),
            "network": {
                "proxy": net.proxy_url(),
                "system_proxy_ignored": not net.proxy_url(),
                "retry": net.retry_config()[0],
                "retry_interval": net.retry_config()[1],
            },
            "agent": core.get_state(),
        })

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

    for endpoint in ("/api/repo/bind", "/api/repo/update",
                     "/api/repo/unbind", "/api/repo/workflow", "/api/records", "/api/user/info"):
        def make_view(ep=endpoint):
            def view():
                return _proxy_raw(ep)
            return view
        app.add_url_rule(endpoint, endpoint=f"proxy_{endpoint.strip('/').replace('/', '_')}",
                         view_func=make_view(), methods=["POST"])

    # 仓库列表：远端数据 + 本地令牌绑定情况（细粒度令牌按仓库保存）
    @app.route("/api/repo/list", methods=["POST"])
    def repo_list_annotated():
        try:
            resp = remote.call("/api/repo/list", request.get_json(silent=True) or {}, raw=True)
        except Exception as e:
            return jsonify({"message": f"远端请求失败：{e}"}), 502
        try:
            body = resp.json()
        except ValueError:
            return jsonify({"message": resp.text or "unknown"}), resp.status_code
        if resp.status_code >= 400:
            return jsonify(body), resp.status_code
        global_tokens = paths.load_global_tokens()
        repo_tokens = paths.load_repo_tokens_map()
        for r in body.get("repos", []):
            repo_full = f"{r.get('owner', '')}/{r.get('name', '')}"
            repo_list = repo_tokens.get(repo_full, [])
            r["repo_token_hints"] = paths.token_hint_list(repo_list)
            r["has_repo_token"] = bool(repo_list)
            r["repo_token_hint"] = paths.mask(repo_list[0]) if repo_list else ""
            r["token_source"] = "repo" if repo_list else ("global" if global_tokens else "")
            r["token_count"] = len(repo_list) + (0 if repo_list else len(global_tokens))
            r["global_token_count"] = len(global_tokens)
        return jsonify(body), resp.status_code

    # ---------------------------------------------------------------
    # 凭据（GitHub Token / LLM）：存服务端（加密），本机只读缓存
    # ---------------------------------------------------------------
    @app.route("/api/local/credentials", methods=["POST"])
    def credentials_overview():
        """配置页用：脱敏列表（顺序即回退顺序）+ 本机缓存状态。"""
        try:
            items = credentials.list_items()
            error = ""
        except Exception as e:
            items, error = [], str(e)
        cached = paths.load_cred_cache()
        return jsonify({
            "message": "OK",
            "credentials": items,
            "github_tokens": [c for c in items
                              if c.get("kind") == "github_token" and not c.get("repo")],
            "llm": [c for c in items if c.get("kind") == "llm"],
            "cached": {
                "github_tokens": len(cached.get("github_tokens") or []),
                "llm": len(cached.get("llm") or []),
                "repo_tokens": sum(len(v) for v in (cached.get("repo_tokens") or {}).values()),
            },
            "cache_disabled": paths.cred_cache_disabled(),
            "error": error,
        })

    @app.route("/api/local/credentials/sync", methods=["POST"])
    def credentials_sync_now():
        try:
            data = credentials.sync(force=True)
        except Exception as e:
            return jsonify({"message": f"同步失败：{e}"}), 502
        return jsonify({"message": "OK",
                        "github_token_count": len(data.get("github_tokens") or []),
                        "llm_count": len(data.get("llm") or [])})

    @app.route("/api/local/tokens", methods=["POST"])
    def global_tokens_manage():
        """全局（传统）令牌：增删与顺序调整都写服务端。"""
        data = request.get_json(silent=True) or {}
        action = (data.get("action") or "add").strip()
        try:
            if action == "add":
                token = (data.get("token") or "").strip()
                if not token:
                    return jsonify({"message": "token missing"}), 400
                credentials.add_token(token, name=(data.get("name") or "").strip())
            elif action == "remove":
                credentials.remove_item(data.get("id"))
            elif action == "move":
                credentials.move_item(data.get("id"),
                                      "up" if int(data.get("delta", -1)) < 0 else "down")
            else:
                return jsonify({"message": "unknown action"}), 400
        except remote.RemoteError as e:
            return jsonify({"message": e.message}), e.code
        except Exception as e:
            return jsonify({"message": f"保存失败：{e}"}), 502
        tokens = paths.load_global_tokens()
        return jsonify({
            "message": "OK",
            "github_tokens": paths.token_hint_list(tokens),
            "github_token_count": len(tokens),
            "github_configured": bool(tokens),
        })

    @app.route("/api/local/llm", methods=["POST"])
    def llm_manage():
        """LLM 配置：多条并存，顺序即回退顺序；统一存服务端。"""
        data = request.get_json(silent=True) or {}
        action = (data.get("action") or "add").strip()
        try:
            if action == "add":
                key = (data.get("api_key") or "").strip()
                if not key:
                    return jsonify({"message": "api_key missing"}), 400
                credentials.add_llm(key, (data.get("base_url") or "").strip(),
                                    (data.get("model") or "").strip(),
                                    (data.get("name") or "").strip())
            elif action == "update":
                credentials.update_item(data.get("id"),
                                        value=(data.get("api_key") or "").strip() or None,
                                        name=data.get("name"),
                                        base_url=(data.get("base_url") or "").strip(),
                                        model=(data.get("model") or "").strip())
            elif action == "remove":
                credentials.remove_item(data.get("id"))
            elif action == "move":
                credentials.move_item(data.get("id"),
                                      "up" if int(data.get("delta", -1)) < 0 else "down")
            else:
                return jsonify({"message": "unknown action"}), 400
        except remote.RemoteError as e:
            return jsonify({"message": e.message}), e.code
        except Exception as e:
            return jsonify({"message": f"保存失败：{e}"}), 502
        configs = paths.load_llm_configs()
        return jsonify({
            "message": "OK",
            "llm_count": len(configs),
            "llm_configured": bool(configs),
            "llm_api_key_hint": paths.mask(configs[0]["api_key"]) if configs else "",
            "llm_base_url": configs[0]["base_url"] if configs else "",
            "llm_model": configs[0]["model"] if configs else "",
        })

    # ---------------------------------------------------------------
    # 仓库专属令牌（细粒度）：同样存服务端，用 extra.repo 标记归属
    # ---------------------------------------------------------------
    @app.route("/api/local/repo-token", methods=["POST"])
    def repo_token_manage():
        data = request.get_json(silent=True) or {}
        owner = (data.get("owner") or "").strip()
        name = (data.get("name") or "").strip()
        if not owner or not name:
            return jsonify({"message": "owner or name missing"}), 400
        repo_full = f"{owner}/{name}"
        action = (data.get("action") or ("clear" if data.get("clear") else "add")).strip()
        try:
            if action == "add":
                token = (data.get("token") or "").strip()
                if not token:
                    return jsonify({"message": "token missing"}), 400
                credentials.add_token(token, name=(data.get("label") or ""), repo_full=repo_full)
            elif action == "remove":
                credentials.remove_item(data.get("id"))
            elif action == "clear":
                credentials.clear_repo(repo_full)
            else:
                return jsonify({"message": "unknown action"}), 400
        except remote.RemoteError as e:
            return jsonify({"message": e.message}), e.code
        except Exception as e:
            return jsonify({"message": f"保存失败：{e}"}), 502
        tokens = paths.load_repo_tokens(repo_full)
        return jsonify({
            "message": "OK",
            "has_repo_token": bool(tokens),
            "repo_token_hints": paths.token_hint_list(tokens),
            "repo_token_hint": paths.mask(tokens[0]) if tokens else "",
        })

    # ---------------------------------------------------------------
    # 仓库检查器：workflow 是否已安装（本地令牌调用，Token 不出本机）
    # ---------------------------------------------------------------
    @app.route("/api/repo/check-workflow", methods=["POST"])
    def check_workflow():
        data = request.get_json(silent=True) or {}
        owner = (data.get("owner") or "").strip()
        name = (data.get("name") or "").strip()
        if not owner or not name:
            return jsonify({"message": "owner or name missing"}), 400
        repo_full = f"{owner}/{name}"
        candidates = paths.resolve_tokens(repo_full)
        if not candidates:
            return jsonify({
                "message": "OK", "installed": False, "up_to_date": False, "file": "",
                "files": [], "token_source": "", "tried": 0,
                "reason": "未配置令牌（可在仓库绑定页填写细粒度令牌，或在本地配置填写传统令牌）",
            })
        try:
            from GithubTool import user as gh_user
        except Exception as e:
            return jsonify({"message": "OK", "installed": False, "up_to_date": False, "file": "",
                            "files": [], "token_source": "", "tried": 0,
                            "reason": f"GitHub 组件不可用：{e}"})
        last_reason = ""
        last_source = candidates[-1][1]
        tried = 0
        for token, source in candidates:
            tried += 1
            last_source = source
            res = gh_user.list_workflows(token, repo_full)
            if res.get("retryable"):
                last_reason = res.get("reason", "")
                continue  # 换下一个令牌重试
            files = res.get("files", [])
            matched = next((f for f in files if f.get("is_codevoyage")), None)
            return jsonify({
                "message": "OK",
                "installed": bool(matched),
                "up_to_date": bool(matched and matched.get("has_author_check")),
                "file": matched["name"] if matched else "",
                "files": files,
                "token_source": source,
                "tried": tried,
                "reason": res.get("reason", ""),
            })
        return jsonify({
            "message": "OK", "installed": False, "up_to_date": False, "file": "",
            "files": [], "token_source": last_source, "tried": tried,
            "reason": f"已尝试 {tried} 个令牌均失败：{last_reason}",
        })

    # ---------------------------------------------------------------
    # 一键安装：本地提交 workflow 文件并发起 PR（令牌不出本机）
    # 优先使用该仓库绑定的细粒度令牌，否则回退全局传统令牌
    # ---------------------------------------------------------------
    @app.route("/api/repo/install-workflow", methods=["POST"])
    def install_workflow():
        data = request.get_json(silent=True) or {}
        owner = (data.get("owner") or "").strip()
        name = (data.get("name") or "").strip()
        bind_id = data.get("id")
        if not owner or not name or not bind_id:
            return jsonify({"message": "owner/name/id missing"}), 400
        token_candidates = paths.resolve_tokens(f"{owner}/{name}")
        # 1) 取远端生成的 workflow 文本（以绑定的关键词/白名单为准，无需令牌）
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

        # 2) 没有令牌：无法用 API 建 PR（GitHub 强制鉴权），给出网页提交方案（效果等同 PR）
        if not token_candidates:
            return jsonify({
                "message": "OK", "ok": False, "token_source": "", "tried": 0,
                "reason": "该仓库没有可用令牌，无法通过 API 创建 PR（GitHub 要求鉴权）。可改用网页提交，效果等同。",
                "fallback": {
                    "filename": filename,
                    "path": path,
                    "content": content,
                    "repo_url": f"https://github.com/{owner}/{name}",
                    "new_file_urls": [
                        f"https://github.com/{owner}/{name}/new/main?filename={path}",
                        f"https://github.com/{owner}/{name}/new/master?filename={path}",
                    ],
                },
            })

        # 3) 依次尝试各令牌，第一个成功即返回（提交身份取本地配置的提交邮箱）
        try:
            from GithubTool import user as gh_user
        except Exception as e:
            return jsonify({"message": "OK", "ok": False, "reason": f"GitHub 组件不可用：{e}"})
        git_name, git_email = paths.git_identity()
        errors = []
        for index, (token, source) in enumerate(token_candidates, start=1):
            try:
                res = gh_user.commit_workflow_pr(
                    token, f"{owner}/{name}", path, content,
                    author_name=git_name, author_email=git_email,
                )
            except Exception as e:
                errors.append(f"第 {index} 个令牌（{source}）：{e}")
                continue
            if res.get("unchanged"):
                return jsonify({
                    "message": "OK", "ok": True, "unchanged": True, "existed": True,
                    "pr_url": "", "branch": res["branch"], "filename": filename, "path": path,
                    "reason": "仓库中的 workflow 与当前配置完全一致，已是最新，无需重复提交 PR。",
                    "token_source": source, "tried": index, "author_email": git_email,
                    "steps": res.get("steps", []),
                })
            return jsonify({
                "message": "OK", "ok": True, "pr_url": res["pr_url"], "branch": res["branch"],
                "filename": filename, "path": path, "existed": res["existed"],
                "token_source": source, "tried": index, "author_email": git_email,
                "steps": res.get("steps", []),
            })
        return jsonify({
            "message": "OK", "ok": False, "token_source": token_candidates[-1][1],
            "tried": len(token_candidates),
            "reason": "已尝试全部令牌均失败：\n" + "\n".join(errors),
        })

    # ---------------------------------------------------------------
    # 本机配置：提交身份（令牌与 LLM 已改存服务端，见 /api/local/tokens、/api/local/llm）
    # ---------------------------------------------------------------
    @app.route("/api/local/config", methods=["POST"])
    def local_config_save():
        data = request.get_json(silent=True) or {}
        allowed = {"git_name", "git_email"}
        clear_keys = [k for k in (data.get("clear") or [])
                      if k in ("llm_api_key", "llm_base_url", "llm_model")]
        if clear_keys:
            paths.clear_local_conf(clear_keys)
        update = {k: str(v) for k, v in data.items() if k in allowed and v is not None}
        if update:
            paths.save_local_conf(update)  # 增量合并写入，避免覆盖其它键
        return jsonify({
            "message": "OK",
            "llm_configured": bool(paths.load_llm_configs()),
            "git_name": paths.git_identity()[0],
            "git_email": paths.git_identity()[1],
        })

    # ---------------------------------------------------------------
    # 本地 GitHub 状态：校验令牌（传统/细粒度）+ 指定仓库权限
    # ---------------------------------------------------------------
    @app.route("/api/local/github-status", methods=["POST"])
    def github_status():
        data = request.get_json(silent=True) or {}
        owner = (data.get("owner") or "").strip()
        name = (data.get("name") or "").strip()
        if owner and name:
            candidates = paths.resolve_tokens(f"{owner}/{name}")
        else:
            candidates = [(t, "global") for t in paths.load_global_tokens()]
        if not candidates:
            return jsonify({"message": "OK", "configured": False, "ok": False, "kind": "", "login": "",
                            "scopes": [], "hints": [], "repo": None, "token_source": "",
                            "token_count": 0, "tried": 0,
                            "reason": "未配置令牌：可在「仓库绑定」填写细粒度令牌，或在「本地配置」填写传统令牌"})
        try:
            from GithubTool import user as gh_user
        except Exception as e:
            return jsonify({"message": "OK", "configured": True, "ok": False, "kind": "", "login": "",
                            "scopes": [], "hints": [], "repo": None, "token_source": candidates[0][1],
                            "token_count": len(candidates), "tried": 0,
                            "reason": f"GitHub 组件不可用：{e}"})

        last_payload = None
        for index, (token, source) in enumerate(candidates, start=1):
            info = gh_user.describe_token(token)
            payload = {
                "message": "OK",
                "configured": True,
                "ok": info["ok"],
                "kind": info["kind"],
                "login": info.get("login", ""),
                "scopes": info.get("scopes", []),
                "hints": gh_user.TOKEN_HINTS.get(info["kind"], []),
                "repo": None,
                "token_source": source,
                "token_count": len(candidates),
                "tried": index,
                "reason": info.get("reason", ""),
            }
            if not info["ok"]:
                last_payload = payload
                continue  # 换下一个令牌
            if owner and name:
                access = gh_user.repo_access(token, f"{owner}/{name}")
                payload["repo"] = access
                if not access.get("ok"):
                    payload["reason"] = access.get("reason", "")
                    last_payload = payload
                    if access.get("retryable"):
                        continue  # 该令牌看不到这个仓库，尝试下一个
                    return jsonify(payload)
            return jsonify(payload)
        if last_payload is not None:
            return jsonify(last_payload)
        return jsonify({"message": "OK", "configured": True, "ok": False, "kind": "", "login": "",
                        "scopes": [], "hints": [], "repo": None, "token_source": "",
                        "token_count": len(candidates), "tried": len(candidates),
                        "reason": "已尝试全部令牌均失败"})

    # ---------------------------------------------------------------
    # GitHub 延迟实时探测（前端定时刷新）
    # ---------------------------------------------------------------
    @app.route("/api/local/github-latency", methods=["POST"])
    def github_latency():
        """探测到 GitHub 的往返延迟：api = PyGithub 接口，git = clone/push 传输。"""
        probes = (("api", "https://api.github.com/"), ("git", "https://github.com/"))
        out: dict = {}

        def _run(key: str, url: str) -> None:
            out[key] = net.latency(url)

        threads = []
        for key, url in probes:
            t = threading.Thread(target=_run, args=(key, url), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=12)
        for key, _url in probes:
            out.setdefault(key, {"ok": False, "status": 0, "ms": 0, "reason": "探测超时"})
        return jsonify({"message": "OK", **out})

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

    # ---------------------------------------------------------------
    # 执行轨迹（AI 思考 / 工具调用 / 回答）：实时与历史
    # ---------------------------------------------------------------
    @app.route("/api/Agent/trace", methods=["POST"])
    def agent_trace():
        data = request.get_json(silent=True) or {}
        repo_full = (data.get("repo_full") or "").strip()
        uid = (data.get("uuid") or "").strip()
        if not repo_full or not uid:
            return jsonify({"message": "repo_full or uuid missing"}), 400
        found = trace.load(repo_full, uid)
        if not found:
            # 旧版本任务没落轨迹、或轨迹已被清理：属正常状态，返回 200 让前端友好展示，
            # 不要用 404 —— 前端会把它当成「出错」，在项目对话里弹红色告警。
            return jsonify({"message": "OK", "found": False, "trace": None})
        return jsonify({"message": "OK", "found": True, "trace": found})

    @app.route("/api/Agent/trace/live", methods=["POST"])
    def agent_trace_live():
        """当前正在执行任务的轨迹（供实时轮询）。"""
        state = core.get_state()
        repo_full = (state.get("repo_full") or "").strip()
        uid = (state.get("running_uuid") or "").strip()
        if not repo_full or not uid:
            return jsonify({"message": "OK", "running": False, "trace": None})
        return jsonify({"message": "OK", "running": True, "trace": trace.load(repo_full, uid)})

    # ---------------------------------------------------------------
    # GitHub 代理（跨客户端互助）：状态 / 开关 / 自检
    # 安全默认：借用代连（enabled）与作为代连节点（as_helper）都默认关闭；
    # 开启 as_helper 必须带 confirm=true（显式授权），否则忽略。
    # ---------------------------------------------------------------
    @app.route("/api/proxy/status", methods=["POST"])
    def proxy_status():
        data = request.get_json(silent=True) or {}
        if any(k in data for k in ("enabled", "as_helper")):
            proxy.configure(enabled=data.get("enabled"), as_helper=data.get("as_helper"),
                            confirm=bool(data.get("confirm")))
            proxy.ensure_started()
        st = proxy.status()
        nodes, stats, error = [], {}, ""
        try:
            body, _ = remote.call("/api/proxy/nodes", {}, timeout=20)
            nodes = body.get("nodes") or []
            stats = body.get("stats") or {}
        except Exception as e:
            error = str(e)
        return jsonify({
            "message": "OK",
            "local": st,
            "proxy_url": proxy.local_proxy_url(),
            "active": proxy.active(),
            "mode": "proxy" if proxy.in_proxy_mode() else "direct",
            "helper_authorized": proxy.helper_authorized(),
            "allowed_targets": sorted(proxy.allowed_targets()),
            "nodes": nodes,
            "stats": stats,
            "error": error,
        })

    @app.route("/api/proxy/test", methods=["POST"])
    def proxy_test():
        """自检：经隧道访问一次 api.github.com，验证打洞 / 中继是否可用。"""
        import time as _time

        started = _time.time()
        try:
            sock, via = proxy.open_tunnel("api.github.com:443")
            try:
                sock.close()
            except OSError:
                pass
            return jsonify({"message": "OK", "ok": True, "via": via,
                            "seconds": round(_time.time() - started, 2)})
        except Exception as e:
            return jsonify({"message": "OK", "ok": False, "reason": str(e),
                            "seconds": round(_time.time() - started, 2)})

    # ---------------------------------------------------------------
    # AI 对话（多会话）：向已配置的 LLM 提问，历史存本机
    # ---------------------------------------------------------------
    @app.route("/api/chat/list", methods=["POST"])
    def chat_list():
        return jsonify({"message": "OK", "conversations": chat.list_conversations()})

    @app.route("/api/chat/create", methods=["POST"])
    def chat_create():
        data = request.get_json(silent=True) or {}
        return jsonify({"message": "OK", "conversation": chat.create(data.get("name") or "")})

    @app.route("/api/chat/get", methods=["POST"])
    def chat_get():
        data = request.get_json(silent=True) or {}
        conv = chat.get(data.get("id"))
        if not conv:
            return jsonify({"message": "会话不存在"}), 404
        return jsonify({"message": "OK", "conversation": conv})

    @app.route("/api/chat/remove", methods=["POST"])
    def chat_remove():
        data = request.get_json(silent=True) or {}
        return jsonify({"message": "OK", "removed": chat.remove(data.get("id"))})

    @app.route("/api/chat/rename", methods=["POST"])
    def chat_rename():
        data = request.get_json(silent=True) or {}
        conv = chat.rename(data.get("id"), data.get("name") or "")
        if not conv:
            return jsonify({"message": "会话不存在"}), 404
        return jsonify({"message": "OK", "conversation": conv})

    @app.route("/api/chat/for-repo", methods=["POST"])
    def chat_for_repo():
        """取某仓库的对话（ID = 仓库名哈希，每个仓库唯一，没有就建）。"""
        data = request.get_json(silent=True) or {}
        repo_full = (data.get("repo_full") or "").strip()
        if not repo_full:
            return jsonify({"message": "repo_full missing"}), 400
        conv = chat.get_by_repo(repo_full)
        if not conv:
            return jsonify({"message": "会话创建失败"}), 500
        return jsonify({"message": "OK", "conversation": conv})

    @app.route("/api/chat/ask", methods=["POST"])
    def chat_ask():
        """提问必须带会话 ID；仓库对话的 ID 由 /api/chat/for-repo 解析得到。"""
        data = request.get_json(silent=True) or {}
        cid = (data.get("id") or "").strip()
        if not cid:
            return jsonify({"message": "缺少对话 ID"}), 400
        if not chat.get(cid):
            return jsonify({"message": "会话不存在，请重新打开该对话"}), 404
        try:
            result = chat.ask(cid, data.get("text") or "")
        except chat.ChatError as e:
            return jsonify({"message": str(e)}), 502
        except Exception as e:
            return jsonify({"message": f"提问失败：{e}"}), 502
        return jsonify({"message": "OK", **result})

    @app.route("/api/Agent/logs", methods=["GET"])
    def agent_logs():
        return jsonify({"message": "OK", "logs": paths.tail_log(500)})
