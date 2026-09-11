"""远端调度中心数据库层。

兼容两种后端：
- 未配置 DATABASE_URL 或指向文件路径：SQLite（默认，零依赖）；
- DATABASE_URL 形如 postgres://...：PostgreSQL（需安装 psycopg2-binary）。

SQL 统一使用 ? 占位符，PostgreSQL 连接时自动转成 %s；插入统一走 RETURNING id。
"""
import hashlib
import json
import os
import sqlite3
import uuid as uuid_mod
from contextlib import contextmanager
from datetime import datetime, timezone

ISSUE_STATES = ("wait", "ready", "putout", "ok", "failed")


def _db_setting():
    """返回 (engine, dsn)。engine ∈ {'sqlite','postgres'}。"""
    raw = (os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")
        os.makedirs(base, exist_ok=True)
        return "sqlite", os.path.join(base, "codevoyage.db")
    if raw.startswith("postgres"):
        return "postgres", raw
    if raw.startswith("sqlite"):
        raw = raw.split("://", 1)[1] if "://" in raw else raw
    return "sqlite", raw


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_digest(raw: str) -> str:
    return _sha(raw)


def token_of(email: str, uid: int, raw_password: str) -> str:
    """Token = hash(Email, ID, Password) 的十六进制摘要。"""
    return _sha(f"{email}:{uid}:{raw_password}")


# ------------------------------------------------------------------
#  连接适配：让 sqlite3 / psycopg2 的用法统一为
#  with _conn() as conn: conn.execute(sql, params).fetchone()/fetchall()
# ------------------------------------------------------------------
class _CursorView:
    def __init__(self, cursor, columns):
        self._cur = cursor
        self._cols = columns
        self.rowcount = cursor.rowcount

    def _row(self, row):
        if row is None:
            return None
        return {c: row[i] for i, c in enumerate(self._cols)}

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._cur.fetchall()]


class _Conn:
    """封装后的连接对象：execute 返回 _CursorView。"""

    def __init__(self, engine, raw_conn):
        self.engine = engine
        self.raw = raw_conn

    def execute(self, sql: str, params=None):
        if self.engine == "postgres":
            cursor = self.raw.cursor()
            cursor.execute(sql.replace("?", "%s"), params or [])
            cols = [d[0] for d in cursor.description] if cursor.description else []
            return _CursorView(cursor, cols)
        cursor = self.raw.execute(sql, params or [])
        cols = [d[0] for d in cursor.description] if cursor.description else []
        return _CursorView(cursor, cols)

    def executescript(self, statements):
        for sql in statements:
            self.execute(sql)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


@contextmanager
def _conn():
    engine, dsn = _db_setting()
    if engine == "postgres":
        try:
            import psycopg2  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "DATABASE_URL 使用 PostgreSQL 但未安装驱动，请执行: pip install psycopg2-binary"
            ) from e
        raw = psycopg2.connect(dsn)
    else:
        raw = sqlite3.connect(dsn)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
    conn = _Conn(engine, raw)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------
#  建表
# ------------------------------------------------------------------
def _ddl(engine: str) -> list:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine == "sqlite" else "SERIAL PRIMARY KEY"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id           {pk},
            email        TEXT    NOT NULL UNIQUE,
            password     TEXT    NOT NULL,
            token_digest TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL
        )""",
        f"""
        CREATE TABLE IF NOT EXISTS repos (
            id         {pk},
            owner      TEXT    NOT NULL,
            name       TEXT    NOT NULL,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            keywords   TEXT    NOT NULL DEFAULT '[]',
            authors    TEXT    NOT NULL DEFAULT '[]',
            created_at TEXT    NOT NULL,
            UNIQUE(owner, name)
        )""",
        f"""
        CREATE TABLE IF NOT EXISTS issues (
            id             {pk},
            uuid           TEXT    NOT NULL UNIQUE,
            user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            repo_id        INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            repo_full      TEXT    NOT NULL,
            issue_number   INTEGER NOT NULL,
            issue_url      TEXT    NOT NULL,
            title          TEXT    NOT NULL DEFAULT '',
            body           TEXT    NOT NULL DEFAULT '',
            comments_json  TEXT    NOT NULL DEFAULT '[]',
            trigger_word   TEXT    NOT NULL DEFAULT '',
            trigger_source TEXT    NOT NULL DEFAULT 'issue',
            state          TEXT    NOT NULL DEFAULT 'wait',
            pr_url         TEXT    NOT NULL DEFAULT '',
            error          TEXT    NOT NULL DEFAULT '',
            created_at     TEXT    NOT NULL,
            updated_at     TEXT    NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_issues_user_state ON issues(user_id, state)",
        f"""
        CREATE TABLE IF NOT EXISTS credentials (
            id         {pk},
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind       TEXT    NOT NULL,
            name       TEXT    NOT NULL DEFAULT '',
            secret     TEXT    NOT NULL DEFAULT '',
            extra      TEXT    NOT NULL DEFAULT '{{}}',
            position   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT    NOT NULL,
            updated_at TEXT    NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_credentials_user_kind ON credentials(user_id, kind, position)",
    ]


def init_db() -> None:
    engine, _ = _db_setting()
    with _conn() as conn:
        conn.executescript(_ddl(engine))
        _migrate(conn, engine)


def _migrate(conn, engine: str) -> None:
    """老库升级：为 repos 补充 authors 列（已存在时忽略）。"""
    if engine == "postgres":
        # postgres 事务中一条语句失败会污染整个事务，使用 IF NOT EXISTS 避免报错
        conn.execute("ALTER TABLE repos ADD COLUMN IF NOT EXISTS authors TEXT NOT NULL DEFAULT '[]'")
        return
    try:
        conn.execute("ALTER TABLE repos ADD COLUMN authors TEXT NOT NULL DEFAULT '[]'")
    except Exception:
        pass


# ------------------------------ 用户 ------------------------------
def create_user(email: str, raw_password: str) -> dict | None:
    email = (email or "").strip().lower()
    if not email or not raw_password:
        return None
    digest = password_digest(raw_password)
    with _conn() as conn:
        try:
            row = conn.execute(
                "INSERT INTO users(email, password, token_digest, created_at)"
                " VALUES(?,?,?,?) RETURNING id",
                (email, digest, "", _now()),
            ).fetchone()
        except Exception:
            return None  # email 唯一冲突等
        if not row:
            return None
        token = token_of(email, row["id"], raw_password)
        conn.execute("UPDATE users SET token_digest=? WHERE id=?", (token, row["id"]))
        return conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()


def get_user_by_email(email: str) -> dict | None:
    email = (email or "").strip().lower()
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()


def get_user_by_id(uid: int) -> dict | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def verify_password(user: dict, raw_password: str) -> bool:
    return bool(user) and password_digest(raw_password or "") == user.get("password")


def check_user(uid, token) -> dict | None:
    """校验 ID + token（token 为注册/登录时下发的 Token 摘要）。"""
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return None
    user = get_user_by_id(uid)
    if not user:
        return None
    if not token or not user.get("token_digest"):
        return None
    if str(token) != user["token_digest"]:
        return None
    return user


# ------------------------------ 仓库绑定 ------------------------------
def create_repo_binding(owner: str, name: str, user_id: int, keywords: list, authors: list | None = None) -> dict | None:
    owner = (owner or "").strip()
    name = (name or "").strip()
    if not owner or not name:
        return None
    with _conn() as conn:
        row = conn.execute(
            "INSERT INTO repos(owner, name, user_id, keywords, authors, created_at)"
            " VALUES(?,?,?,?,?,?) RETURNING id",
            (
                owner, name, user_id,
                json.dumps(keywords or [], ensure_ascii=False),
                json.dumps(authors or [], ensure_ascii=False),
                _now(),
            ),
        ).fetchone()
        if not row:
            return None  # 唯一约束冲突 -> 已绑定
        return conn.execute("SELECT * FROM repos WHERE id=?", (row["id"],)).fetchone()


def get_binding_by_repo(owner: str, name: str) -> dict | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM repos WHERE owner=? AND name=?", (owner, name)
        ).fetchone()


def get_binding_by_id(bid: int) -> dict | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM repos WHERE id=?", (bid,)).fetchone()


def list_repos(user_id: int) -> list:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM repos WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()


def update_repo_keywords(bid: int, keywords: list, authors: list | None = None) -> bool:
    """更新关键词；authors 传 None 表示不改动白名单。"""
    with _conn() as conn:
        if authors is None:
            cur = conn.execute(
                "UPDATE repos SET keywords=? WHERE id=?",
                (json.dumps(keywords or [], ensure_ascii=False), bid),
            )
        else:
            cur = conn.execute(
                "UPDATE repos SET keywords=?, authors=? WHERE id=?",
                (
                    json.dumps(keywords or [], ensure_ascii=False),
                    json.dumps(authors or [], ensure_ascii=False),
                    bid,
                ),
            )
        return cur.rowcount > 0


def delete_repo_binding(bid: int, user_id: int) -> bool:
    with _conn() as conn:
        return conn.execute(
            "DELETE FROM repos WHERE id=? AND user_id=?", (bid, user_id)
        ).rowcount > 0


# ------------------------------ Issue 任务 ------------------------------
def _issue_dict(row: dict | None) -> dict | None:
    """行 -> 业务 dict：解析 comments_json 为列表。"""
    if row is None:
        return None
    d = dict(row)
    try:
        d["comments"] = json.loads(d.pop("comments_json"))
    except (TypeError, json.JSONDecodeError):
        d["comments"] = []
    return d


def create_issue(binding: dict, payload: dict) -> dict | None:
    """把事件写入 issues 表，状态 wait，并赋予 UUID。"""
    owner = binding["owner"]
    name = binding["name"]
    repo_full = f"{owner}/{name}"
    issue_number = int(payload.get("issue_number") or 0)
    issue_url = f"https://github.com/{repo_full}/issues/{issue_number}"
    comments = payload.get("all_comments") or []
    uid = uuid_mod.uuid4().hex
    with _conn() as conn:
        row = conn.execute(
            """INSERT INTO issues
               (uuid, user_id, repo_id, repo_full, issue_number, issue_url, title, body,
                comments_json, trigger_word, trigger_source, state, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (
                uid, binding["user_id"], binding["id"], repo_full, issue_number, issue_url,
                payload.get("issue_title") or "", json.dumps(payload, ensure_ascii=False),
                json.dumps(comments, ensure_ascii=False),
                payload.get("hit_trigger_word") or "",
                payload.get("trigger_source") or "issue",
                "wait", _now(), _now(),
            ),
        ).fetchone()
        if not row:
            return None
        return _issue_dict(conn.execute("SELECT * FROM issues WHERE uuid=?", (uid,)).fetchone())


def claim_next_issue(user_id: int) -> dict | None:
    """把该用户最早一条 wait 的 Issue 标记为 ready 并返回，作为下发结果。"""
    with _conn() as conn:
        issue = conn.execute(
            "SELECT * FROM issues WHERE user_id=? AND state='wait' ORDER BY id ASC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not issue:
            return None
        conn.execute(
            "UPDATE issues SET state='ready', updated_at=? WHERE id=?",
            (_now(), issue["id"]),
        )
        return _issue_dict(conn.execute("SELECT * FROM issues WHERE uuid=?", (issue["uuid"],)).fetchone())


def get_active_issue(binding_id: int, issue_number: int) -> dict | None:
    """同仓库同一编号且未完结的任务，避免 webhook 重复入库。"""
    with _conn() as conn:
        return _issue_dict(conn.execute(
            "SELECT * FROM issues WHERE repo_id=? AND issue_number=? AND state IN ('wait','ready')"
            " ORDER BY id DESC LIMIT 1",
            (binding_id, issue_number),
        ).fetchone())


def get_issue_by_uuid(uid: str) -> dict | None:
    with _conn() as conn:
        return _issue_dict(conn.execute("SELECT * FROM issues WHERE uuid=?", (uid,)).fetchone())


def update_issue_result(uid: str, state: str, pr_url: str = "", error: str = "") -> dict | None:
    """回执结果。state 需为 putout/ok/failed 等。"""
    if state not in ("wait", "ready", "putout", "ok", "failed"):
        return None
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE issues SET state=?, pr_url=?, error=?, updated_at=? WHERE uuid=?",
            (state, pr_url, error, _now(), uid),
        )
        if cur.rowcount == 0:
            return None
        return _issue_dict(conn.execute("SELECT * FROM issues WHERE uuid=?", (uid,)).fetchone())


def list_issues(user_id: int, state: str | None = None) -> list:
    with _conn() as conn:
        if state:
            if state == "active":
                rows = conn.execute(
                    "SELECT * FROM issues WHERE user_id=? AND state IN ('wait','ready') ORDER BY id DESC",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM issues WHERE user_id=? AND state=? ORDER BY id DESC",
                    (user_id, state),
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM issues WHERE user_id=? ORDER BY id DESC", (user_id,)
            ).fetchall()
        return [_issue_dict(r) for r in rows]


def count_issues(binding_id: int) -> int:
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM issues WHERE repo_id=?", (binding_id,)).fetchone()
        return row["c"] if row else 0


# ------------------------------ 凭据（GitHub Token / LLM） ------------------------------
CRED_KINDS = ("github_token", "llm")


def _cred_dict(row) -> dict | None:
    if row is None:
        return None
    try:
        extra = json.loads(row["extra"] or "{}")
    except Exception:
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"] or "",
        "secret": row["secret"] or "",
        "extra": extra,
        "position": row["position"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_credentials(user_id: int, kind: str | None = None) -> list:
    with _conn() as conn:
        if kind:
            rows = conn.execute(
                "SELECT * FROM credentials WHERE user_id=? AND kind=? ORDER BY position, id",
                (user_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM credentials WHERE user_id=? ORDER BY kind, position, id", (user_id,)
            ).fetchall()
        return [c for c in (_cred_dict(r) for r in rows) if c]


def get_credential(user_id: int, cid) -> dict | None:
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return None
    with _conn() as conn:
        return _cred_dict(conn.execute(
            "SELECT * FROM credentials WHERE id=? AND user_id=?", (cid, user_id)
        ).fetchone())


def add_credential(user_id: int, kind: str, secret: str, name: str = "", extra: dict | None = None) -> dict | None:
    if kind not in CRED_KINDS or not secret:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS m FROM credentials WHERE user_id=? AND kind=?",
            (user_id, kind),
        ).fetchone()
        pos = (row["m"] if row else -1) + 1
        new_id = conn.execute(
            """INSERT INTO credentials (user_id, kind, name, secret, extra, position, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?) RETURNING id""",
            (user_id, kind, name or "", secret, json.dumps(extra or {}, ensure_ascii=False),
             pos, _now(), _now()),
        ).fetchone()
        if not new_id:
            return None
        return _cred_dict(conn.execute(
            "SELECT * FROM credentials WHERE id=?", (new_id["id"],)
        ).fetchone())


def update_credential(user_id: int, cid, secret: str | None = None, name: str | None = None,
                      extra: dict | None = None) -> dict | None:
    current = get_credential(user_id, cid)
    if not current:
        return None
    with _conn() as conn:
        conn.execute(
            "UPDATE credentials SET secret=?, name=?, extra=?, updated_at=? WHERE id=? AND user_id=?",
            (
                secret if secret is not None else current["secret"],
                name if name is not None else current["name"],
                json.dumps(extra if extra is not None else current["extra"], ensure_ascii=False),
                _now(),
                current["id"],
                user_id,
            ),
        )
        return _cred_dict(conn.execute(
            "SELECT * FROM credentials WHERE id=?", (current["id"],)
        ).fetchone())


def delete_credential(user_id: int, cid) -> bool:
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return False
    with _conn() as conn:
        cur = conn.execute("DELETE FROM credentials WHERE id=? AND user_id=?", (cid, user_id))
        return cur.rowcount > 0


def move_credential(user_id: int, cid, direction: str) -> bool:
    """在同类型凭据内上移/下移一位，用于调整回退顺序。"""
    if direction not in ("up", "down"):
        return False
    with _conn() as conn:
        current = _cred_dict(conn.execute(
            "SELECT * FROM credentials WHERE id=? AND user_id=?", (cid, user_id)
        ).fetchone())
        if not current:
            return False
        if direction == "up":
            other = conn.execute(
                """SELECT * FROM credentials WHERE user_id=? AND kind=? AND (position < ? OR (position = ? AND id < ?))
                   ORDER BY position DESC, id DESC LIMIT 1""",
                (user_id, current["kind"], current["position"], current["position"], current["id"]),
            ).fetchone()
        else:
            other = conn.execute(
                """SELECT * FROM credentials WHERE user_id=? AND kind=? AND (position > ? OR (position = ? AND id > ?))
                   ORDER BY position, id LIMIT 1""",
                (user_id, current["kind"], current["position"], current["position"], current["id"]),
            ).fetchone()
        if not other:
            return False
        other = dict(other)
        conn.execute("UPDATE credentials SET position=? WHERE id=? AND user_id=?",
                     (other["position"], current["id"], user_id))
        conn.execute("UPDATE credentials SET position=? WHERE id=? AND user_id=?",
                     (current["position"], other["id"], user_id))
        return True

