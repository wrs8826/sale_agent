"""飞书长连接机器人。

使用 lark-oapi SDK 的 WebSocket 长连接模式接收消息，无需公网域名。
消息到达后调 QA 图生成回复，再通过官方 SDK 发回飞书。

配置文件：agent_service/mcp/lark_mcp.json
  必填：app_id, app_secret
  选填：verification_token, encrypt_key（长连接模式不强制要求）

使用：
    from agent_service.mcp.lark_bot import lark_bot
    lark_bot.on_status_change(callback)
    lark_bot.start()   # app 启动时调用一次，幂等
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_CONFIG_PATH = Path(__file__).resolve().parent / "lark_mcp.json"

STATE_IDLE    = "idle"
STATE_RUNNING = "running"
STATE_ERROR   = "error"


def _load_cfg() -> Dict[str, Any]:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


class LarkBot:
    def __init__(self) -> None:
        self._state = STATE_IDLE
        self._error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._client: Optional[Any] = None   # lark.Client，用于发消息
        self._started = False
        self._lock = threading.Lock()

    # ── 外部接口 ──────────────────────────────────────────────────────────────

    def on_status_change(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        self._callbacks.append(cb)

    def get_status(self) -> Dict[str, Any]:
        return {"state": self._state, "error": self._error}

    def start(self) -> None:
        """启动后台长连接线程（幂等）。"""
        with self._lock:
            if self._started:
                return
            self._started = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="lark-bot")
        self._thread.start()

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    def _notify(self) -> None:
        status = self.get_status()
        for cb in self._callbacks:
            try:
                cb(status)
            except Exception as exc:
                print(f"[lark_bot] 回调异常: {exc}")

    def _run(self) -> None:
        try:
            import lark_oapi as lark
        except ImportError:
            self._state = STATE_ERROR
            self._error = "缺少依赖：pip install lark-oapi"
            self._notify()
            return

        try:
            cfg = _load_cfg()
        except Exception as exc:
            self._state = STATE_ERROR
            self._error = f"读取 lark_mcp.json 失败: {exc}"
            self._notify()
            return

        app_id     = cfg.get("app_id", "")
        app_secret = cfg.get("app_secret", "")
        encrypt_key        = cfg.get("encrypt_key", "")
        verification_token = cfg.get("verification_token", "")

        if not app_id or not app_secret:
            self._state = STATE_ERROR
            self._error = "lark_mcp.json 中 app_id / app_secret 不能为空"
            self._notify()
            return

        # 构建 API 客户端（发消息用）
        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

        # 注册消息事件处理器
        handler = (
            lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
            .register_p2_im_message_receive_v1(self._on_p2p_message)
            .build()
        )

        # 启动 WebSocket 长连接（阻塞，SDK 内部自动重连）
        ws_client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )

        self._state = STATE_RUNNING
        self._notify()
        print(f"[lark_bot] 长连接已启动，App ID: {app_id}")
        ws_client.start()   # 阻塞直到进程退出

    # ── 消息处理 ──────────────────────────────────────────────────────────────

    def _on_p2p_message(self, data: Any) -> None:
        """收到 P2P 消息时由 SDK 回调，立即开新线程处理，避免阻塞 SDK 心跳。"""
        print(f"[lark_bot] 收到消息回调: {data}")
        threading.Thread(
            target=self._reply_async,
            args=(data,),
            daemon=True,
        ).start()

    def _reply_async(self, data: Any) -> None:
        """后台线程：提取文本 → 调 QA 图（带历史） → 持久化 → 通过 SDK 回复飞书。"""
        try:
            msg        = data.event.message
            message_id = msg.message_id
            chat_id    = getattr(msg, "chat_id", "") or ""
            content    = json.loads(msg.content or "{}").get("text", "").strip()

            # 提取发送者 open_id（历史存储的用户维度标识）
            try:
                open_id = data.event.sender.sender_id.open_id or ""
            except Exception:
                open_id = ""

            # 清理 @ 标记（群消息中会出现 @_user_xxx）
            content = re.sub(r"@\S+", "", content).strip()
            if not content:
                return

            reply_text = self._query(content, open_id=open_id, chat_id=chat_id)
            if not reply_text:
                return

            # 持久化本轮对话（写失败不影响回复）
            if open_id or chat_id:
                try:
                    from agent_service.mcp.lark_history import append_turn
                    append_turn(open_id, chat_id, content, reply_text)
                except Exception as exc:
                    print(f"[lark_bot] 历史写入失败（不影响回复）: {exc}")

            self._send_reply(message_id, reply_text)

        except Exception as exc:
            print(f"[lark_bot] 消息处理失败: {exc}")

    def _query(self, text: str, *, open_id: str = "", chat_id: str = "") -> str:
        """生成回复文本。

        优先路径：MCP 就绪 → ReAct Agent（具备飞书工具调用能力）
        降级路径：MCP 未就绪 → RAG QA 图（纯知识库问答）
        """
        # lazy import 避免循环依赖（pitfall #13）
        from api import services

        chat_cfg = services.load_chat_settings()
        if not chat_cfg.get("api_key"):
            print("[lark_bot] chat API key 未配置，跳过回复")
            return ""

        # 加载对话历史（读失败以空历史继续，不中断服务）
        history: list = []
        if open_id or chat_id:
            try:
                from agent_service.mcp.lark_history import load_history
                history = load_history(open_id, chat_id)
            except Exception as exc:
                print(f"[lark_bot] 历史加载失败（以空历史继续）: {exc}")

        # ── 优先路径：飞书 MCP 工具 Agent ────────────────────────────────────
        from agent_service.mcp.mcp_manager import mcp_manager
        if mcp_manager.get_status()["state"] == "ready":
            try:
                return mcp_manager.run_agent_sync(text, chat_cfg, history=history)
            except Exception as exc:
                print(f"[lark_bot] MCP Agent 调用失败，降级到 QA 图: {exc}")

        # ── 降级路径：RAG QA 图 ───────────────────────────────────────────────
        from agent_service.graph import build_qa_graph

        try:
            cfg = services.load_config()
            rag, _ = services.get_rag(cfg.chunk_size, cfg.chunk_overlap, None)
        except Exception:
            rag = None

        def rag_fn(q: str, k: int):
            cur = services.get_current_rag()
            if cur is None:
                return []
            try:
                return cur.search(q, top_k=k)
            except Exception:
                return []

        state = {
            "query": text,
            "history": history,
            "chat_cfg": chat_cfg,
            "rag_fn": rag_fn if rag else None,
            "top_k": 5,
            "score_threshold": services.load_rag_threshold(),
        }

        full_text = ""
        try:
            for event in build_qa_graph().stream(state, stream_mode="custom"):
                if event.get("type") == "done":
                    full_text = event.get("full_text", "")
        except Exception as exc:
            print(f"[lark_bot] QA 图调用失败: {exc}")

        return full_text

    def _send_reply(self, message_id: str, text: str) -> None:
        """通过飞书 SDK 回复指定消息。"""
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .msg_type("text")
                .build()
            )
            .build()
        )

        response = self._client.im.v1.message.reply(request)
        if not response.success():
            print(f"[lark_bot] 发送回复失败: code={response.code} msg={response.msg}")


lark_bot = LarkBot()
