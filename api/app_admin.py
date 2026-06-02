"""管理员端 Flask 入口。

启动：
    python -m api.app_admin
默认监听 0.0.0.0:5002。

权限规则：仅 admin 角色账号可登录；普通用户账号被拒绝。
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
from api.agent import bp as agent_bp
from api.auth import bp as auth_bp
from api.conversations import bp as conversations_bp
from api.knowledge import bp as knowledge_bp
from api.settings import bp as settings_bp
from api.users import bp as users_bp
from api.lark_agent import bp as lark_agent_bp
from api.socketio_instance import socketio
from agent_service.mcp.mcp_manager import mcp_manager
from agent_service.mcp.lark_bot import lark_bot

WEB_DIR = _PROJECT_ROOT / "web"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.secret_key = os.getenv("ADMIN_SECRET_KEY", "admin-app-secret-key-change-in-prod")
    app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 7

    DOCS_DIR.mkdir(exist_ok=True)
    WIKI_DIR.mkdir(exist_ok=True)
    CONVERSATIONS_DIR.mkdir(exist_ok=True)

    app.register_blueprint(auth_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(conversations_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(lark_agent_bp)

    socketio.init_app(app)
    mcp_manager.start()
    lark_bot.start()

    def _admin_check():
        return session.get("user_id") and session.get("role") == "admin"

    @app.route("/")
    def index():
        if _admin_check():
            return redirect("/admin")
        return send_from_directory(str(WEB_DIR / "admin"), "login.html")

    @app.route("/admin")
    def admin_index():
        if not _admin_check():
            return redirect("/")
        return send_from_directory(str(WEB_DIR / "admin"), "knowledge.html")

    @app.route("/admin/knowledge")
    def admin_knowledge():
        if not _admin_check():
            return redirect("/")
        return send_from_directory(str(WEB_DIR / "admin"), "knowledge.html")

    @app.route("/admin/chat")
    def admin_chat():
        if not _admin_check():
            return redirect("/")
        return send_from_directory(str(WEB_DIR / "admin"), "chat.html")

    @app.route("/admin/users")
    def admin_users():
        if not _admin_check():
            return redirect("/")
        return send_from_directory(str(WEB_DIR / "admin"), "users.html")

    @app.route("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(str(WEB_DIR / "assets"), filename)

    return app


app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5002, debug=False)
