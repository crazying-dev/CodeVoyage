"""本地中心模块。

main() 在 5431 端口启动 Flask：
- API 路由（centre.API.register）
- Agent 可视化控制台静态托管（centre.app.register_static，前端产物在 CodeVoyage/dist）
"""
import centre.API as API  # noqa: F401
import centre.app as app_module


def create_app():
    application = app_module.create_app()
    API.register(application)
    app_module.register_static(application)
    return application


def main():
    app = create_app()
    # Flask 调试重载器在子线程中不可用，这里显式关闭
    app.run(host="127.0.0.1", port=5431, debug=False, threaded=True, use_reloader=False)
