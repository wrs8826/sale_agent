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

from flask import Blueprint, jsonify, request
from flask_socketio import Namespace

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
