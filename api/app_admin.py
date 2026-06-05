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
from api.session_store import configure_session
from api.conv_stats import ensure_table as ensure_stats_table
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
ADMIN_DIST = _PROJECT_ROOT / "web-admin" / "dist"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.secret_key = os.getenv("ADMIN_SECRET_KEY", "admin-app-secret-key-change-in-prod")
    configure_session(app, key_prefix="admin_sess:")

    DOCS_DIR.mkdir(exist_ok=True)
    WIKI_DIR.mkdir(exist_ok=True)
    CONVERSATIONS_DIR.mkdir(exist_ok=True)
    ensure_stats_table()

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

    # React SPA 静态资源（hash 命名，永久缓存）
    @app.route("/assets/<path:filename>")
    def spa_assets(filename):
        # 优先从 React build 的 assets/ 目录响应
        dist_assets = ADMIN_DIST / "assets"
        if (dist_assets / filename).exists():
            return send_from_directory(str(dist_assets), filename)
        # 兜底：用户端共享 CSS/JS（user.html 等仍需要）
        return send_from_directory(str(WEB_DIR / "assets"), filename)

    # 所有非 API 路由均返回 React SPA 入口（客户端路由）
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def spa_index(path):
        # API 蓝图已优先匹配，此处只处理前端路由
        index_file = ADMIN_DIST / "index.html"
        if not index_file.exists():
            return (
                "<h2>管理员前端未构建</h2>"
                "<p>请先执行：<code>cd web-admin && npm run build</code></p>",
                503,
            )
        return send_from_directory(str(ADMIN_DIST), "index.html")

    return app


app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5002, debug=False)
