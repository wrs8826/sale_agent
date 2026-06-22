"""用户端 Flask 入口。

启动：
    python -m api.app_user
默认监听 0.0.0.0:5001。

权限规则：user 和 admin 账号均可登录；只能访问用户界面。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, redirect, send_from_directory, session

from agent_service import CONVERSATIONS_DIR, DOCS_DIR, WIKI_DIR
from agent_service.logging_config import setup_logging, get_logger
from api.session_store import configure_session
from api.conv_stats import ensure_table as ensure_stats_table
from api.agent import bp as agent_bp
from api.auth import bp as auth_bp
from api.conversations import bp as conversations_bp
from api.knowledge import bp as knowledge_bp
from api.settings import bp as settings_bp
from api.lark_agent import bp as lark_agent_bp
from api.socketio_instance import socketio
from agent_service.mcp.mcp_manager import mcp_manager
from agent_service.mcp.lark_bot import lark_bot

WEB_DIR = _PROJECT_ROOT / "web"


def create_app() -> Flask:
    setup_logging()   # 最先初始化日志，级别由 config.yaml log_level / 环境变量 LOG_LEVEL 控制
    get_logger(__name__).info("用户端启动中…")

    app = Flask(__name__, static_folder=None)
    app.secret_key = os.getenv("USER_SECRET_KEY", "user-app-secret-key-change-in-prod")
    configure_session(app, key_prefix="user_sess:")

    DOCS_DIR.mkdir(exist_ok=True)
    WIKI_DIR.mkdir(exist_ok=True)
    CONVERSATIONS_DIR.mkdir(exist_ok=True)
    ensure_stats_table()

    app.register_blueprint(auth_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(conversations_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(lark_agent_bp)

    socketio.init_app(app)
    mcp_manager.start()
    lark_bot.start()

    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect("/user")
        return send_from_directory(str(WEB_DIR), "login.html")

    @app.route("/user")
    def user_page():
        if not session.get("user_id"):
            return redirect("/")
        return send_from_directory(str(WEB_DIR), "user.html")

    @app.route("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(str(WEB_DIR / "assets"), filename)

    return app


app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5001, debug=False)
