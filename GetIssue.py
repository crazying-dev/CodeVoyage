"""GetIssue 组件：长轮询远端，把新 Issue 转发给本地 5431 中心入库。

- 读取 ~/.CodeVoyage/user/conf.json 拿到 ID/token；
- POST {远端}/api/GetIssue {ID, token, Action: GetIssue} 长轮询；
- 命中（message == 1）时把整个返回体 POST 给本地 centre /api/GetIssue；
- 未登录时静默等待，登录由可视化控制台完成。
"""
import json
import time

import centre.net as net
import centre.paths as paths

CENTRE_URL = "http://127.0.0.1:5431"
POLL_INTERVAL = 3


def _read_conf():
    try:
        with open(paths.USER_CONF_PATH, "r", encoding="utf-8") as f:
            conf = json.load(f)
        if conf.get("ID") and conf.get("token"):
            return conf
    except (OSError, ValueError):
        pass
    return None


def main():
    paths.ensure_dirs()
    while True:
        conf = _read_conf()
        if not conf:
            time.sleep(5)
            continue
        try:
            resp = net.request(
                "post",
                paths.remote_base() + "/api/GetIssue",
                timeout=30,
                json={"ID": conf["ID"], "token": conf["token"], "Action": "GetIssue"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("message") == "1" and data.get("Issue"):
                    try:
                        centre_resp = net.request(
                            "post",
                            CENTRE_URL + "/api/GetIssue",
                            timeout=10,
                            json=data,
                        )
                        if centre_resp.status_code == 200:
                            paths.append_log(f"GetIssue 入库成功: {data['Issue'].get('repo_full', '')}#{data['Issue'].get('issue_number', '')}")
                    except Exception as e:
                        paths.append_log(f"GetIssue 转发 5431 失败: {e}")
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            paths.append_log(f"GetIssue 拉取失败: {net.friendly_error(e)}")
            time.sleep(5)
