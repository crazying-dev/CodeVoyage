"""远端调用封装（本地代理）。

本地 centre 对远端服务（默认 https://CodeVoyage.yjlt.top，可用 centre.json 的 remote
或环境变量 REMOTE_BASE 覆盖）发起请求。账号/仓库绑定/记录等数据以远端为主，
本模块负责在请求体中自动附带本地保存的 ID + token。
"""
import json

import requests

import centre.paths as paths

DEFAULT_TIMEOUT = 60


class RemoteError(Exception):
    def __init__(self, message: str, code: int = 500, data=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data


def _conf_payload(payload: dict | None) -> dict:
    data = dict(payload or {})
    conf = paths.load_user_conf()
    if conf:
        data.setdefault("ID", conf.get("ID"))
        data.setdefault("token", conf.get("token"))
    return data


def call(endpoint: str, payload: dict | None = None, timeout: int = DEFAULT_TIMEOUT, raw: bool = False):
    """POST JSON 到远端。raw=False 时返回 (json_body, status)；raw=True 返回 requests.Response。"""
    url = paths.remote_base() + endpoint
    resp = requests.post(url, json=_conf_payload(payload), timeout=timeout)
    if raw:
        return resp
    try:
        body = resp.json()
    except ValueError:
        body = {"message": resp.text or "unknown error"}
    if resp.status_code >= 400:
        raise RemoteError(str(body.get("message") or body), resp.status_code, body)
    return body, resp.status_code


def login(email: str, password: str) -> dict:
    """登录远端并把凭证写入本地 conf.json，返回 {ID, token, email}。"""
    body, _ = call("/api/user/login", {"email": email, "password": password}, raw=False)
    return paths.save_user_conf(body["ID"], body["token"], body.get("email", email))


def register(email: str, password: str) -> dict:
    body, _ = call("/api/user/register", {"email": email, "password": password})
    return paths.save_user_conf(body["ID"], body["token"], body.get("email", email))


def user_info() -> dict:
    body, _ = call("/api/user/info", {})
    return body


def forward(endpoint: str, payload: dict | None = None):
    """把请求原样转发给远端（自动附带凭证）。"""
    body, status = call(endpoint, payload)
    return body, status
