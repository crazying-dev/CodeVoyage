"""CodeVoyage 远端调度中心（Flask）。

职责：
1. 用户注册 / 登录，登录成功返回 Token（Email+ID+Password 的哈希摘要），并发登录提醒邮件；
2. 仓库绑定（每仓库仅能绑定一个用户）+ 关键词管理 + workflow 文件生成；
3. 接收 GitHub Workflow 上报的 /api/Github/Issue 事件并入库（状态 wait，赋予 UUID）；
4. GetIssue 长轮询：客户端 Agent 拉取任务，命中后置为 ready 下发；
5. 接收客户端执行回执（OK / PutOut / Failed），完成记录可随时查询；
6. 邮件通知（配置化 SMTP）。

前端网页（Vue 构建产物 dist）由下方静态路由托管，页面内容不属于本文件职责。
"""
import json
import os
import time

from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

# 加载当前目录下的 .env 文件
load_dotenv()

import database  # noqa: E402
import mail  # noqa: E402
import vault  # noqa: E402

app = Flask(__name__, static_folder=None)
database.init_db()

# ---------------- 通用工具 ----------------


def _ok(data: dict, code: int = 200):
    return jsonify(data), code


def _fail(message: str, code: int = 400):
    return jsonify({"message": message}), code


def _auth_or_abort():
    """从 JSON 请求体解析 ID + token 并校验，失败抛错返回 401。"""
    data = request.get_json(silent=True) or {}
    user = database.check_user(data.get("ID"), data.get("token"))
    if not user:
        raise PermissionError()
    return user


@app.errorhandler(PermissionError)
def _auth_error(e):
    return _fail("auth error", 401)


# ==================================================================
#  用户：注册 / 登录
# ==================================================================
@app.route("/api/user/register", methods=["POST"])
def user_register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or len(password) < 6:
        return _fail("email or password invalid")
    user = database.create_user(email, password)
    if not user:
        return _fail("email already exists", 409)
    return _ok({"message": "OK", "ID": user["id"], "token": user["token_digest"], "email": user["email"]})


@app.route("/api/user/login", methods=["POST"])
def user_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    user = database.get_user_by_email(email)
    if not user or not database.verify_password(user, password):
        return _fail("email or password error", 401)
    # 登录提醒邮件（SMTP 未配置时自动跳过）
    mail.login_reminder(user["email"], user["email"])
    return _ok({"message": "OK", "ID": user["id"], "token": user["token_digest"], "email": user["email"]})


@app.route("/api/user/info", methods=["POST"])
def user_info():
    user = _auth_or_abort()
    return _ok({"message": "OK", "ID": user["id"], "email": user["email"]})


# ==================================================================
#  仓库绑定 / 关键词 / workflow
# ==================================================================
def _as_list(value, strip_at: bool = False) -> list:
    """把 keywords/authors 统一解析成去空字符串列表；authors 允许带 @ 前缀。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = value.replace("，", ",").split(",")
    out = []
    for v in (value or []):
        item = str(v).strip()
        if strip_at:
            item = item.lstrip("@")
        if item:
            out.append(item)
    return out


def _parse_repo_payload(data):
    owner = (data.get("owner") or data.get("repo_owner") or "").strip().lstrip("@")
    name = (data.get("name") or data.get("repo_name") or "").strip()
    keywords = _as_list(data.get("keywords"))
    authors = _as_list(data.get("authors"), strip_at=True)
    return owner, name, keywords, authors


def _repo_dict(r: dict) -> dict:
    try:
        r["keywords"] = json.loads(r.pop("keywords"))
    except Exception:
        r["keywords"] = []
    try:
        r["authors"] = json.loads(r.pop("authors"))
    except Exception:
        r["authors"] = []
    return r


@app.route("/api/repo/list", methods=["POST"])
def repo_list():
    user = _auth_or_abort()
    repos = [_repo_dict(r) for r in database.list_repos(user["id"])]
    for r in repos:
        r["issue_count"] = database.count_issues(r["id"])
    return _ok({"message": "OK", "repos": repos})


@app.route("/api/repo/bind", methods=["POST"])
def repo_bind():
    user = _auth_or_abort()
    owner, name, keywords, authors = _parse_repo_payload(request.get_json(silent=True) or {})
    if not owner or not name:
        return _fail("owner or name missing")
    existed = database.get_binding_by_repo(owner, name)
    if existed and existed["user_id"] != user["id"]:
        return _fail("repo already bound by another user", 409)
    if existed:  # 同用户重复绑定视为更新关键词与白名单
        database.update_repo_keywords(existed["id"], keywords, authors)
        return _ok({"message": "OK", "id": existed["id"]})
    binding = database.create_repo_binding(owner, name, user["id"], keywords, authors)
    if not binding:
        return _fail("repo already bound", 409)
    return _ok({"message": "OK", "id": binding["id"]})


@app.route("/api/repo/update", methods=["POST"])
def repo_update():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    binding = database.get_binding_by_id(data.get("id")) if data.get("id") else None
    if not binding or binding["user_id"] != user["id"]:
        return _fail("binding not found", 404)
    _, _, keywords, authors = _parse_repo_payload(data)
    database.update_repo_keywords(binding["id"], keywords, authors)
    return _ok({"message": "OK"})


@app.route("/api/repo/unbind", methods=["POST"])
def repo_unbind():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    if not data.get("id"):
        return _fail("id missing")
    if not database.delete_repo_binding(data["id"], user["id"]):
        return _fail("binding not found", 404)
    return _ok({"message": "OK"})


# ==================================================================
#  用户凭据（GitHub Token / LLM 配置）：服务端加密存储
#  列表接口只返回脱敏值；明文只在 /reveal 中返回给已登录的本人。
# ==================================================================
KIND_LABELS = {"github_token": "GitHub Token", "llm": "LLM API"}


def _cred_public(cred: dict) -> dict:
    secret = ""
    try:
        secret = vault.decrypt(cred["secret"])
    except Exception:
        secret = ""
    item = {
        "id": cred["id"],
        "kind": cred["kind"],
        "label": KIND_LABELS.get(cred["kind"], cred["kind"]),
        "name": cred["name"],
        "masked": vault.masked(secret),
        "position": cred["position"],
        "created_at": cred["created_at"],
    }
    if cred["kind"] == "llm":
        extra = cred.get("extra") or {}
        item["base_url"] = extra.get("base_url", "")
        item["model"] = extra.get("model", "")
    return item


@app.route("/api/user/credentials/list", methods=["POST"])
def credentials_list():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip() or None
    items = [_cred_public(c) for c in database.list_credentials(user["id"], kind)]
    return _ok({"message": "OK", "credentials": items, "crypto": vault.backend()})


@app.route("/api/user/credentials/add", methods=["POST"])
def credentials_add():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip()
    value = (data.get("value") or "").strip()
    if kind not in database.CRED_KINDS:
        return _fail("kind invalid")
    if not value:
        return _fail("值不能为空")
    extra = {}
    if kind == "llm":
        extra = {
            "base_url": (data.get("base_url") or "").strip(),
            "model": (data.get("model") or "").strip(),
        }
    cred = database.add_credential(user["id"], kind, vault.encrypt(value),
                                   (data.get("name") or "").strip(), extra)
    if not cred:
        return _fail("保存失败")
    return _ok({"message": "OK", "credential": _cred_public(cred)})


@app.route("/api/user/credentials/update", methods=["POST"])
def credentials_update():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    current = database.get_credential(user["id"], data.get("id"))
    if not current:
        return _fail("credential not found", 404)
    extra = None
    if current["kind"] == "llm":
        extra = dict(current["extra"])
        if "base_url" in data:
            extra["base_url"] = (data.get("base_url") or "").strip()
        if "model" in data:
            extra["model"] = (data.get("model") or "").strip()
    value = str(data.get("value") or "").strip()
    cred = database.update_credential(
        user["id"], current["id"],
        secret=vault.encrypt(value) if value else None,
        name=(data.get("name") or "").strip() or None,
        extra=extra,
    )
    return _ok({"message": "OK", "credential": _cred_public(cred)})


@app.route("/api/user/credentials/remove", methods=["POST"])
def credentials_remove():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    return _ok({"message": "OK", "removed": database.delete_credential(user["id"], data.get("id"))})


@app.route("/api/user/credentials/move", methods=["POST"])
def credentials_move():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    moved = database.move_credential(user["id"], data.get("id"), (data.get("direction") or "").strip())
    return _ok({"message": "OK", "moved": moved})


@app.route("/api/user/credentials/reveal", methods=["POST"])
def credentials_reveal():
    """取回明文，仅供已登录本人的本机 Agent 调用。"""
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    cred = database.get_credential(user["id"], data.get("id"))
    if not cred:
        return _fail("credential not found", 404)
    try:
        value = vault.decrypt(cred["secret"])
    except Exception as e:
        return _fail(f"解密失败：{e}")
    return _ok({"message": "OK", "id": cred["id"], "kind": cred["kind"],
                "value": value, "extra": cred["extra"]})


# 生成给用户放置到仓库 .github/workflows/ 的 workflow 模板
WORKFLOW_TEMPLATE = """\
name: CodeVoyage Issue Report
on:
  issues:
    types: [opened, edited]
  issue_comment:
    types: [created, edited]

permissions:
  issues: read

jobs:
  forward_event:
    runs-on: ubuntu-latest
    steps:
      - name: set key list
        id: trigger_words
        env:
          KEYWORDS_JSON: __KEYWORDS__
        run: |
          echo "trigger_list=$KEYWORDS_JSON" >> $GITHUB_OUTPUT

      - name: collect issue info
        id: extract
        env:
          EVENT_NAME: ${{ github.event_name }}
          FULL_REPOSITORY: ${{ github.repository }}
          REPO_OWNER_IN: ${{ github.repository_owner }}
          ISSUE_NUMBER_IN: ${{ github.event.issue.number }}
          ISSUE_TITLE_IN: ${{ github.event.issue.title }}
          ISSUE_BODY_IN: ${{ github.event.issue.body }}
          ISSUE_AUTHOR_IN: ${{ github.event.issue.user.login }}
          COMMENT_BODY_IN: ${{ github.event.comment.body }}
          COMMENT_AUTHOR_IN: ${{ github.event.comment.user.login }}
        run: |
          REPO_OWNER="$REPO_OWNER_IN"
          REPO_NAME="${FULL_REPOSITORY#*/}"
          ISSUE_NUMBER="${ISSUE_NUMBER_IN:-0}"
          ISSUE_TITLE="$ISSUE_TITLE_IN"
          ISSUE_BODY="$ISSUE_BODY_IN"
          ISSUE_AUTHOR="$ISSUE_AUTHOR_IN"
          if [ "$EVENT_NAME" = "issue_comment" ]; then
            TRIGGER_CONTENT="$COMMENT_BODY_IN"
            TRIGGER_SOURCE="comment"
            COMMENT_AUTHOR="$COMMENT_AUTHOR_IN"
          else
            TRIGGER_CONTENT="$ISSUE_TITLE_IN $ISSUE_BODY_IN"
            TRIGGER_SOURCE="issue"
            COMMENT_AUTHOR=""
          fi
          {
            echo "repo_owner=$REPO_OWNER"
            echo "repo_name=$REPO_NAME"
            echo "issue_number=$ISSUE_NUMBER"
            echo "issue_title=$ISSUE_TITLE"
            echo "issue_author=$ISSUE_AUTHOR"
            echo "comment_author=$COMMENT_AUTHOR"
            echo "trigger_source=$TRIGGER_SOURCE"
            echo "issue_body<<CV_EOF"
            echo "$ISSUE_BODY"
            echo "CV_EOF"
            echo "trigger_content<<CV_EOF"
            echo "$TRIGGER_CONTENT"
            echo "CV_EOF"
          } >> "$GITHUB_OUTPUT"

      - name: warn when no keywords
        if: steps.trigger_words.outputs.trigger_list == '[]'
        run: echo "::warning::未配置触发关键词，CodeVoyage 不会触发；请到控制台重新生成 workflow"

      - name: find keyword
        id: match_keyword
        env:
          TRIGGER_LIST: ${{ steps.trigger_words.outputs.trigger_list }}
          TRIGGER_CONTENT: ${{ steps.extract.outputs.trigger_content }}
        uses: actions/github-script@v7
        with:
          script: |
            const list = JSON.parse(process.env.TRIGGER_LIST || '[]');
            const content = process.env.TRIGGER_CONTENT || '';
            let hit = null;
            for (const w of list) {
              if (w && content.includes(w)) { hit = w; break; }
            }
            core.setOutput('hit', hit ? 'true' : 'false');
            core.setOutput('hit_word', hit || '');

      - name: collect comments
        id: fetch_all_comments
        if: steps.match_keyword.outputs.hit == 'true'
        env:
          REPO_OWNER: ${{ steps.extract.outputs.repo_owner }}
          REPO_NAME: ${{ steps.extract.outputs.repo_name }}
          ISSUE_NUMBER: ${{ steps.extract.outputs.issue_number }}
        uses: actions/github-script@v7
        with:
          script: |
            const { data: comments } = await github.rest.issues.listComments({
              owner: process.env.REPO_OWNER,
              repo: process.env.REPO_NAME,
              issue_number: Number(process.env.ISSUE_NUMBER)
            });
            const simplified = comments.map(c => ({
              user: c.user?.login,
              body: c.body,
              html_url: c.html_url,
              created_at: c.created_at
            }));
            core.setOutput('comments_json', JSON.stringify(simplified));

      - name: push info with POST
        if: steps.match_keyword.outputs.hit == 'true'
        env:
          WEBHOOK_URL: __WEBHOOK_URL__
          WEBHOOK_SECRET: __WEBHOOK_SECRET__
          REPO_OWNER: ${{ steps.extract.outputs.repo_owner }}
          REPO_NAME: ${{ steps.extract.outputs.repo_name }}
          ISSUE_NUMBER: ${{ steps.extract.outputs.issue_number }}
          ISSUE_TITLE: ${{ steps.extract.outputs.issue_title }}
          ISSUE_BODY: ${{ steps.extract.outputs.issue_body }}
          TRIGGER_SOURCE: ${{ steps.extract.outputs.trigger_source }}
          ISSUE_AUTHOR: ${{ steps.extract.outputs.issue_author }}
          COMMENT_AUTHOR: ${{ steps.extract.outputs.comment_author }}
          HIT_WORD: ${{ steps.match_keyword.outputs.hit_word }}
          ALL_COMMENTS: ${{ steps.fetch_all_comments.outputs.comments_json }}
        run: |
          if [ -z "$WEBHOOK_URL" ]; then
            echo "::error::服务端地址为空，请重新在控制台生成并提交 workflow"
            exit 1
          fi
          URL="$WEBHOOK_URL"
          case "$URL" in
            */api/Github/Issue) ;;
            *) URL="${URL%/}/api/Github/Issue" ;;
          esac

          PAYLOAD=$(jq -n \\
            --arg repo_owner "$REPO_OWNER" \\
            --arg repo_name "$REPO_NAME" \\
            --arg issue_number "${ISSUE_NUMBER:-0}" \\
            --arg issue_title "$ISSUE_TITLE" \\
            --arg issue_body "$ISSUE_BODY" \\
            --arg trigger_source "$TRIGGER_SOURCE" \\
            --arg issue_author "$ISSUE_AUTHOR" \\
            --arg comment_author "$COMMENT_AUTHOR" \\
            --arg hit_word "$HIT_WORD" \\
            --argjson all_comments "${ALL_COMMENTS:-[]}" \\
            '{repo_owner: $repo_owner, repo_name: $repo_name, issue_number: ($issue_number|tonumber), issue_title: $issue_title, issue_body: $issue_body, trigger_source: $trigger_source, issue_author: $issue_author, comment_author: $comment_author, hit_trigger_word: $hit_word, all_comments: $all_comments}')

          echo "POST $URL"
          CODE=$(curl -sS -L --post301 --post302 --post303 -o resp.txt -w "%{http_code}" -X POST "$URL" \\
            -H "Content-Type: application/json" \\
            -H "Authorization: Bearer $WEBHOOK_SECRET" \\
            -d "$PAYLOAD")
          echo "HTTP $CODE"
          cat resp.txt
          if [ "$CODE" -lt 200 ] || [ "$CODE" -ge 300 ]; then
            echo "::error::服务端返回 $CODE（地址 $URL），请回到控制台重新生成并提交 workflow"
            exit 1
          fi
"""


def _public_base() -> str:
    """推断写进 workflow 的对外服务地址。

    优先 PUBLIC_BASE_URL；否则用反向代理传的 X-Forwarded-Proto / X-Forwarded-Host；
    两者都没有时，非本机主机默认按 https 处理（TLS 通常终止在 nginx 上，Flask 只看到 http）。
    """
    configured = (os.getenv("PUBLIC_BASE_URL") or "").strip()
    if configured:
        return configured.rstrip("/")
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
    host = (request.headers.get("X-Forwarded-Host") or request.host or "").split(",")[0].strip()
    if not proto:
        hostname = host.split(":")[0]
        is_local = hostname in ("localhost", "127.0.0.1") or hostname.startswith(("192.168.", "10.", "172."))
        proto = request.scheme if is_local else "https"
    return f"{proto}://{host}".rstrip("/")


@app.route("/api/repo/workflow", methods=["POST"])
def repo_workflow():
    """生成该仓库绑定对应的 GitHub Actions workflow 内容。

    服务端地址与签名密钥直接写死在文件里（无需再配置仓库 Secrets）：
    - WEBHOOK_URL = PUBLIC_BASE_URL（或按请求推断的对外地址）+ /api/Github/Issue
    - WEBHOOK_SECRET = 服务端 .env 的 WEBHOOK_SECRET
    """
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    binding = database.get_binding_by_id(data.get("id")) if data.get("id") else None
    if not binding or binding["user_id"] != user["id"]:
        return _fail("binding not found", 404)
    keywords = []
    try:
        keywords = json.loads(binding["keywords"])
    except Exception:
        pass
    keyword_js = "[" + ",".join(json.dumps(k, ensure_ascii=False) for k in keywords) + "]"
    public_base = _public_base()
    webhook_url = f"{public_base}/api/Github/Issue"
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")

    # 用 JSON 字符串形式写入，保证 YAML 转义安全
    yaml_text = WORKFLOW_TEMPLATE.replace("__KEYWORDS__", json.dumps(keyword_js, ensure_ascii=False))
    yaml_text = yaml_text.replace("__WEBHOOK_URL__", json.dumps(webhook_url, ensure_ascii=False))
    yaml_text = yaml_text.replace("__WEBHOOK_SECRET__", json.dumps(webhook_secret, ensure_ascii=False))

    filename = f"codevoyage-{binding['owner']}-{binding['name']}.yml"
    return app.response_class(
        yaml_text,
        mimetype="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-CodeVoyage-Webhook-URL": webhook_url,
        },
    )


# ==================================================================
#  GitHub Workflow 事件上报
# ==================================================================
def _normalize_login(value) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _author_allowed(binding: dict, author) -> bool:
    """触发者校验：白名单(authors) + 仓库 owner 允许；旧版 workflow 未携带作者时放行。"""
    author = _normalize_login(author)
    if not author:
        return True
    try:
        allowed = json.loads(binding.get("authors") or "[]")
    except Exception:
        allowed = []
    allowed_set = {_normalize_login(a) for a in allowed if str(a).strip()}
    allowed_set.add(_normalize_login(binding.get("owner") or ""))
    return author in allowed_set


@app.route("/api/Github/Issue", methods=["POST"])
def github_issue():
    data = request.get_json(silent=True) or {}
    secret = os.getenv("WEBHOOK_SECRET", "")
    got = (request.headers.get("X-CodeVoyage-Secret") or "").strip()
    if not got:
        auth = request.headers.get("Authorization", "")
        got = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    if secret and got != secret:
        return _fail("bad secret", 403)
    owner = (data.get("repo_owner") or "").strip()
    name = (data.get("repo_name") or "").strip()
    binding = database.get_binding_by_repo(owner, name)
    if not binding:
        return _ok({"message": "ignored: repo not bound"})
    # 关键词校验（workflow 已匹配时兜底，防止伪造上报）
    hit = data.get("hit_trigger_word") or ""
    if not hit:
        content = f"{data.get('issue_title') or ''} {data.get('issue_body') or ''}"
        content += " " + " ".join(str(c.get("body", "")) for c in (data.get("all_comments") or []))
        keywords = []
        try:
            keywords = json.loads(binding["keywords"])
        except Exception:
            pass
        for w in keywords:
            if w and w in content:
                hit = w
                break
        if not hit:
            return _ok({"message": "ignored: no keyword"})
        data["hit_trigger_word"] = hit
    # 触发者身份校验：仅白名单用户或仓库 owner 可触发（缺 author 字段的旧版 workflow 放行）
    author = data.get("comment_author") or data.get("issue_author")
    if not _author_allowed(binding, author):
        return _ok({"message": "ignored: author not allowed"})
    try:
        issue_number = int(data.get("issue_number"))
    except (TypeError, ValueError):
        return _fail("issue_number invalid")
    active = database.get_active_issue(binding["id"], issue_number)
    if active:  # 重复上报，直接返回原 UUID
        return _ok({"message": "OK", "uuid": active["uuid"]})
    issue = database.create_issue(binding, data)
    if not issue:
        return _fail("create issue failed")
    return _ok({"message": "OK", "uuid": issue["uuid"]})


# ==================================================================
#  客户端拉取 / 回执
# ==================================================================
def _issue_client_payload(issue: dict) -> dict:
    """下发给客户端 Agent 的轻量 Issue 结构。"""
    payload = {}
    try:
        payload = json.loads(issue.get("body") or "{}")
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "uuid": issue["uuid"],
        "repo_full": issue["repo_full"],
        "issue_number": issue["issue_number"],
        "issue_url": issue["issue_url"],
        "title": issue.get("title") or "",
        "body_text": payload.get("issue_body") or payload.get("body") or "",
        "trigger_word": issue.get("trigger_word") or payload.get("hit_trigger_word") or "",
        "trigger_source": issue.get("trigger_source") or "",
        "comments": issue.get("comments") or [],
    }


@app.route("/api/GetIssue", methods=["POST"])
def get_issue():
    """长轮询。等待该用户存在 wait 的 Issue，命中后置 ready 并下发。"""
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    if data.get("Action") != "GetIssue":
        return _fail("action error")
    deadline = time.time() + 20  # 长轮询上限 20s
    while time.time() < deadline:
        issue = database.claim_next_issue(user["id"])
        if issue:
            return _ok({"message": "1", "Issue": _issue_client_payload(issue)})
        time.sleep(1)
    return _ok({"message": "0"})


@app.route("/api/Issue/Result", methods=["POST"])
def issue_result():
    """客户端执行回执：status = OK / PutOut / Failed。"""
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    uid = data.get("uuid")
    status = (data.get("status") or data.get("Action") or "").strip()
    state = {"OK": "ok", "PutOut": "putout", "Failed": "failed"}.get(status)
    if not uid or not state:
        return _fail("status or uuid invalid")
    issue = database.get_issue_by_uuid(uid)
    if not issue or issue["user_id"] != user["id"]:
        return _fail("issue not found", 404)
    issue = database.update_issue_result(uid, state, data.get("pr_url") or "", data.get("error") or "")
    if not issue:
        return _fail("update failed")
    # 完成/失败通知（SMTP 未配置时自动跳过）
    if state == "ok" and issue["pr_url"]:
        mail.task_done_mail(user["email"], issue["issue_url"], issue["pr_url"], issue["repo_full"], issue["issue_number"])
    elif state == "failed":
        mail.task_failed_mail(user["email"], issue["issue_url"], issue["repo_full"], issue["issue_number"], issue["error"])
    return _ok({"message": "OK"})


@app.route("/api/records", methods=["POST"])
def records():
    user = _auth_or_abort()
    data = request.get_json(silent=True) or {}
    state = (data.get("state") or "").strip() or None
    if state not in (None, "active", "wait", "ready", "putout", "ok", "failed"):
        state = None
    issues = database.list_issues(user["id"], state)
    return _ok({"message": "OK", "records": issues})


# ---------------- Vue SPA 静态资源处理（保持原网页不变） ----------------
DIST_FOLDER = os.path.join(os.path.dirname(__file__), "dist")


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_vue(path):
    if path.startswith("api/"):
        return {"error": "api not found"}, 404
    if not os.path.isdir(DIST_FOLDER):
        return {"error": "frontend not built"}, 404
    file_path = os.path.join(DIST_FOLDER, path)
    if path and os.path.exists(file_path):
        return send_from_directory(DIST_FOLDER, path)
    return send_from_directory(DIST_FOLDER, "index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5431"))
    app.run(debug=True, host="0.0.0.0", port=port)
