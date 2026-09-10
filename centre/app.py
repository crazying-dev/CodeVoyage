"""本地中心 5431 Flask 应用。

- 托管 CodeVoyage 顶层前端构建产物（CodeVoyage/dist）作为可视化控制台；
- 所有 /api/* 接口由 centre.API 注册（登录代理、Agent 队列、仓库与记录代理等）。
"""
import os

from flask import Flask

from centre import paths


def create_app() -> Flask:
    paths.ensure_dirs()
    flask_app = Flask(__name__, static_folder=None)
    return flask_app


DIST_FOLDER = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist"))


def register_static(app: Flask) -> None:
    from flask import send_from_directory

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_console(path: str):
        if path.startswith("api/"):
            return {"error": "api not found"}, 404
        if not os.path.isdir(DIST_FOLDER):
            return {"error": "console not built, run: pnpm build in CodeVoyage root"}, 404
        file_path = os.path.join(DIST_FOLDER, path)
        if path and os.path.isfile(file_path):
            return send_from_directory(DIST_FOLDER, path)
        return send_from_directory(DIST_FOLDER, "index.html")
