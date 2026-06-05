"""飞书 MCP Agent 蓝图。

路由：
    GET  /lark/status  → 当前 MCP 工具加载状态（JSON 轮询）
    POST /lark/chat    → 调用飞书 Agent（同步阻塞，最长 120 s）

WebSocket（flask-socketio）：
    namespace /lark
    事件 lark_status  → 服务端主动推送状态变更

客户端示例（JS）：
    const socket = io('/lark');
    socket.on('lark_status', data => {
        // data: { state, tools, count, error }
        console.log(data.state, data.count, data.tools);
    });
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request
from flask_socketio import Namespace

_LARK_CFG_PATH = Path(__file__).resolve().parent.parent / "agent_service" / "mcp" / "lark_mcp.json"


def _load_lark_cfg() -> dict:
    try:
        return json.loads(_LARK_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

from api import services
from api.socketio_instance import socketio
from agent_service.mcp.mcp_manager import mcp_manager
from agent_service.mcp.lark_bot import lark_bot

bp = Blueprint("lark_agent", __name__)


# ── 状态回调：工具加载完成 / 失败时广播给所有 /lark 连接者 ─────────────────────
def _push_status(status: dict) -> None:
    socketio.emit("lark_status", status, namespace="/lark")


mcp_manager.on_status_change(_push_status)

def _push_bot_status(status: dict) -> None:
    socketio.emit("lark_bot_status", status, namespace="/lark")

lark_bot.on_status_change(_push_bot_status)


# ── SocketIO namespace /lark ──────────────────────────────────────────────────
class LarkNamespace(Namespace):
    def on_connect(self):
        # 新客户端连接时立即推送两个模块的当前状态
        self.emit("lark_status", mcp_manager.get_status())
        self.emit("lark_bot_status", lark_bot.get_status())

    def on_disconnect(self):
        pass


socketio.on_namespace(LarkNamespace("/lark"))


# ── REST 路由 ─────────────────────────────────────────────────────────────────

@bp.route("/lark/status", methods=["GET"])
def lark_status():
    """轮询式状态查询，无需 WebSocket 时使用。"""
    return jsonify(mcp_manager.get_status())


@bp.route("/lark/bot/status", methods=["GET"])
def lark_bot_status():
    """查询飞书长连接机器人状态。"""
    return jsonify(lark_bot.get_status())


@bp.route("/lark/oauth/callback", methods=["GET"])
def lark_oauth_callback():
    """飞书 OAuth 授权回调。

    飞书在用户同意授权后将请求重定向到此地址：
        GET /lark/oauth/callback?code=<授权码>&state=<open_id>

    流程：
        1. 用 code 换取 user_access_token
        2. 将 token 持久化到 lark_tokens/<open_id>.json
        3. 返回成功/失败 HTML 页面（用户在浏览器中看到）
    """
    code  = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()   # 即 open_id

    if not code:
        return _oauth_html("❌ 授权失败", "未收到授权码（code），请重新发送 auth 指令。", success=False), 400

    cfg = _load_lark_cfg()
    app_id     = cfg.get("app_id", "")
    app_secret = cfg.get("app_secret", "")
    redirect_uri = cfg.get("oauth_redirect_uri", "")

    if not app_id or not app_secret:
        return _oauth_html("❌ 配置错误", "服务器未配置飞书应用凭证。", success=False), 500

    try:
        from agent_service.mcp.lark_oauth import exchange_code
        from agent_service.mcp.lark_token_store import save as save_token

        token_data = exchange_code(code, app_id, app_secret, redirect_uri)
        open_id = token_data.get("open_id") or state
        if not open_id:
            return _oauth_html("❌ 授权失败", "无法获取用户 open_id。", success=False), 400

        save_token(open_id, token_data)
        name = token_data.get("name", "")
        print(f"[lark_oauth] {open_id}（{name}）授权成功")
        return _oauth_html(
            "✅ 授权成功",
            f"{'你好，' + name + '！' if name else ''}个人账户已授权，可关闭此页面，"
            f"回到飞书继续对话。",
            success=True,
        )
    except Exception as exc:
        print(f"[lark_oauth] 授权失败: {exc}")
        return _oauth_html("❌ 授权失败", f"换取 token 时出错：{exc}", success=False), 500


def _oauth_html(title: str, body: str, *, success: bool) -> str:
    color = "#16a34a" if success else "#dc2626"
    icon  = "✅" if success else "❌"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{title}</title>
<style>
  body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;
       justify-content:center;min-height:100vh;margin:0;background:#f8fafc}}
  .card{{background:#fff;border-radius:12px;padding:40px 48px;text-align:center;
         box-shadow:0 4px 24px #0001;max-width:400px}}
  .icon{{font-size:48px;margin-bottom:16px}}
  h1{{color:{color};font-size:22px;margin:0 0 12px}}
  p{{color:#64748b;line-height:1.6;margin:0}}
</style>
</head>
<body>
<div class="card">
  <div class="icon">{icon}</div>
  <h1>{title}</h1>
  <p>{body}</p>
</div>
</body>
</html>"""


@bp.route("/lark/chat", methods=["POST"])
def lark_chat():
    """调用飞书 Agent。

    请求体：{ "message": "帮我查今天的日程" }
    响应：  { "reply": "Agent 回复文本" }
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message 不能为空"}), 400

    chat_cfg = services.load_chat_settings()
    if not chat_cfg.get("api_key"):
        return jsonify({"error": "未配置 chat API key，请先在设置中填写"}), 400

    try:
        reply = mcp_manager.run_agent_sync(message, chat_cfg)
        return jsonify({"reply": reply})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
