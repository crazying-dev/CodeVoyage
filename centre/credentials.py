"""凭据（GitHub Token / LLM 配置）统一走服务端存储，本机只保留缓存。

远端接口（CodeVoyage-server/app.py）：
    /api/user/credentials/list | add | update | remove | move | reveal
- list 只返回脱敏值；明文通过 reveal 逐条取回；
- 取回后写入 centre.paths 的本地缓存（混淆落盘），远端不可用时 Agent 仍可继续工作。

首次同步时会把迁移前保存在本机的令牌 / LLM 配置上传到服务端（仅当服务端对应类别为空）。
"""
import threading

from centre import paths, remote

KIND_GITHUB = "github_token"
KIND_LLM = "llm"

_lock = threading.RLock()
_cache: dict | None = None


# ------------------------------ 远端读写 ------------------------------
def list_items(kind: str | None = None) -> list:
    """远端凭据列表（脱敏），不触发 reveal。"""
    payload = {"kind": kind} if kind else {}
    body, _ = remote.call("/api/user/credentials/list", payload)
    return body.get("credentials") or []


def _fetch_from_remote() -> dict:
    """拉取全部凭据明文，组装成本机缓存结构。"""
    github_tokens: list = []
    repo_tokens: dict = {}
    llm: list = []
    for item in list_items():
        try:
            detail, _ = remote.call("/api/user/credentials/reveal", {"id": item["id"]})
        except Exception:
            continue
        value = str(detail.get("value") or "").strip()
        if not value:
            continue
        if item.get("kind") == KIND_GITHUB:
            repo_full = str((detail.get("extra") or {}).get("repo") or "").strip()
            if repo_full:
                repo_tokens.setdefault(repo_full, []).append(value)
            elif value not in github_tokens:
                github_tokens.append(value)
        elif item.get("kind") == KIND_LLM:
            extra = detail.get("extra") or {}
            llm.append({
                "id": item.get("id"),
                "name": item.get("name") or "",
                "api_key": value,
                "base_url": str(extra.get("base_url") or "").strip(),
                "model": str(extra.get("model") or "").strip(),
            })
    return {"github_tokens": github_tokens, "repo_tokens": repo_tokens, "llm": llm}


def sync(force: bool = False) -> dict:
    """同步远端凭据到本地缓存；远端异常时退回上次缓存。"""
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        try:
            data = _fetch_from_remote()
        except Exception as e:
            cached = paths.load_cred_cache()
            _cache = cached
            paths.append_log(f"凭据同步失败，改用本地缓存：{e}")
            return cached
        paths.save_cred_cache(data)
        _cache = data
        return data


def invalidate() -> None:
    """让下一次 sync 重新拉取（写入操作后调用）。"""
    global _cache
    with _lock:
        _cache = None


def _after_write() -> dict:
    return sync(force=True)


# ------------------------------ 写入（成功后自动刷新缓存） ------------------------------
def add_token(value: str, name: str = "", repo_full: str = "") -> dict:
    payload = {"kind": KIND_GITHUB, "value": value, "name": name}
    if repo_full:
        payload["repo"] = repo_full
    remote.call("/api/user/credentials/add", payload)
    return _after_write()


def add_llm(api_key: str, base_url: str = "", model: str = "", name: str = "") -> dict:
    remote.call("/api/user/credentials/add", {
        "kind": KIND_LLM, "value": api_key, "name": name,
        "base_url": base_url, "model": model,
    })
    return _after_write()


def update_item(cid, value: str | None = None, name: str | None = None,
                base_url: str | None = None, model: str | None = None) -> dict:
    payload: dict = {"id": cid}
    if value:
        payload["value"] = value
    if name is not None:
        payload["name"] = name
    if base_url is not None:
        payload["base_url"] = base_url
    if model is not None:
        payload["model"] = model
    remote.call("/api/user/credentials/update", payload)
    return _after_write()


def remove_item(cid) -> dict:
    remote.call("/api/user/credentials/remove", {"id": cid})
    return _after_write()


def move_item(cid, direction: str) -> dict:
    remote.call("/api/user/credentials/move", {"id": cid, "direction": direction})
    return _after_write()


def clear_repo(repo_full: str) -> int:
    """删除某个仓库的全部专属令牌，返回删除条数。"""
    removed = 0
    try:
        for item in list_items(KIND_GITHUB):
            if str(item.get("repo") or "") == repo_full:
                remote.call("/api/user/credentials/remove", {"id": item["id"]})
                removed += 1
    except Exception as e:
        paths.append_log(f"清理仓库令牌失败 {repo_full}：{e}")
        return removed
    if removed:
        _after_write()
    return removed


# ------------------------------ 迁移旧的本机配置 ------------------------------
def migrate_local() -> dict:
    """把迁移前存于本机的令牌 / LLM 配置上传到服务端（服务端对应类别为空时才做）。"""
    result = {"tokens": 0, "repo_tokens": 0, "llm": 0}
    try:
        existing = list_items()
    except Exception:
        return result
    has_github = any(i.get("kind") == KIND_GITHUB for i in existing)
    has_llm = any(i.get("kind") == KIND_LLM for i in existing)
    if not has_github:
        for token in paths._local_global_tokens():
            try:
                add_token(token, name="迁移自本机")
                result["tokens"] += 1
            except Exception:
                pass
        for repo_full, tokens in paths._local_repo_tokens_map().items():
            for token in tokens:
                try:
                    add_token(token, name="迁移自本机", repo_full=repo_full)
                    result["repo_tokens"] += 1
                except Exception:
                    pass
    if not has_llm:
        conf = paths._load_json(paths.LOCAL_CONF_PATH)
        local = {k: paths._decrypt(v) for k, v in conf.items()}
        key = (local.get("llm_api_key") or "").strip()
        if key:
            try:
                add_llm(key, local.get("llm_base_url") or "", local.get("llm_model") or "",
                        name="迁移自本机")
                result["llm"] = 1
            except Exception:
                pass
    if any(result.values()):
        invalidate()
        sync(force=True)
    return result


def startup() -> None:
    """进程启动时调用：迁移旧配置 + 同步缓存（失败不影响启动）。"""
    try:
        migrated = migrate_local()
        if any(migrated.values()):
            paths.append_log(
                f"本机凭据已迁移到服务端：令牌 {migrated['tokens']} 个、"
                f"仓库令牌 {migrated['repo_tokens']} 个、LLM {migrated['llm']} 条"
            )
        sync(force=True)
    except Exception as e:
        paths.append_log(f"凭据初始化失败（改用本地缓存）：{e}")
