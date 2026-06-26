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
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_CONFIG_PATH = Path(__file__).resolve().parent / "lark_mcp.json"

STATE_IDLE    = "idle"
STATE_RUNNING = "running"
STATE_ERROR   = "error"

# 飞书事件是 at-least-once：同一 message_id 可能被重推。最多记忆最近这么多条已处理 id 做去重。
_SEEN_MSG_MAX = 512


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

        # ── 幂等去重：最近已处理的 message_id（OrderedDict 当有界 LRU 用）────────
        self._seen_lock = threading.Lock()
        self._seen_msgs: "OrderedDict[str, None]" = OrderedDict()

        # ── 会话级单飞：按 open_id+chat_id 串行化同会话消息，消除 append_turn 竞态 ─
        self._conv_locks_guard = threading.Lock()
        self._conv_locks: Dict[str, threading.Lock] = {}

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

    def _seen_before(self, message_id: str) -> bool:
        """标记并判断 message_id 是否已处理过（幂等去重）。

        返回 True 表示这是一次重复推送，应直接丢弃；空 id 一律放行（无法去重）。
        采用「检查即标记」原子操作，避免两次重推并发都判为首次。
        """
        if not message_id:
            return False
        with self._seen_lock:
            if message_id in self._seen_msgs:
                self._seen_msgs.move_to_end(message_id)
                return True
            self._seen_msgs[message_id] = None
            if len(self._seen_msgs) > _SEEN_MSG_MAX:
                self._seen_msgs.popitem(last=False)   # 淘汰最旧
            return False

    def _conv_lock(self, key: str) -> threading.Lock:
        """取（必要时创建）某会话维度的锁，用于串行化同会话消息处理。"""
        with self._conv_locks_guard:
            lk = self._conv_locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._conv_locks[key] = lk
            return lk

    def _on_p2p_message(self, data: Any) -> None:
        """收到 P2P 消息时由 SDK 回调，立即开新线程处理，避免阻塞 SDK 心跳。

        在派发前先做 message_id 幂等去重：飞书 at-least-once 会重推同一事件，
        重复的直接丢弃，防止重复回答 + 历史重复写入。
        """
        print(f"[lark_bot] 收到消息回调: {data}")
        try:
            message_id = data.event.message.message_id or ""
        except Exception:
            message_id = ""
        if self._seen_before(message_id):
            print(f"[lark_bot] 重复消息，已忽略: message_id={message_id}")
            return
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

            # ── 会话级单飞：同一 open_id+chat_id 串行处理 ─────────────────────
            # 覆盖「读历史 → 生成 → append_turn」全过程，消除 append_turn 的
            # read-modify-write 竞态（并发会互相覆盖、丢一轮对话）。
            with self._conv_lock(f"{open_id}::{chat_id}"):
                # ── 特殊指令 ──────────────────────────────────────────────────
                cmd = content.strip().lower()
                if cmd == "clear":
                    reply_text = self._save_and_clear(open_id=open_id, chat_id=chat_id)
                    self._send_reply(message_id, reply_text)
                    return
                if cmd == "auth":
                    reply_text = self._send_auth_link(open_id=open_id, message_id=message_id)
                    # _send_auth_link 内部已发送消息卡片，此处只补一条文本回复
                    if reply_text:
                        self._send_reply(message_id, reply_text)
                    return
                if cmd == "deauth":
                    reply_text = self._revoke_auth(open_id=open_id)
                    self._send_reply(message_id, reply_text)
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

    # ── OAuth 相关 ────────────────────────────────────────────────────────────

    def _send_auth_link(self, *, open_id: str, message_id: str) -> str:
        """生成 OAuth 授权链接，以消息卡片形式发给用户。

        返回空字符串表示卡片已发出，调用方不必再额外回复。
        返回非空字符串时作为降级文本回复（例如配置缺失时的错误提示）。
        """
        try:
            cfg = _load_cfg()
            app_id = cfg.get("app_id", "")
            redirect_uri = cfg.get("oauth_redirect_uri", "")
            if not redirect_uri:
                return "⚙️ 未配置 OAuth 回调地址（oauth_redirect_uri），请联系管理员。"

            from agent_service.mcp.lark_oauth import get_auth_url
            url = get_auth_url(app_id, redirect_uri, state=open_id)

            # 发送可点击的消息卡片（Interactive Card）
            card_content = {
                "type": "template",
                "data": {
                    "template_id": "",   # 不使用模板
                }
            }
            # 降级：用普通文本消息发链接
            self._send_reply(
                message_id,
                f"🔐 请点击下方链接完成个人账户授权（有效期 5 分钟）：\n{url}\n\n"
                f"授权后即可使用查询联系人等需要个人权限的功能。",
            )
            return ""   # 已在上方 _send_reply 中发出，外层不再重复发
        except Exception as exc:
            print(f"[lark_bot] 生成授权链接失败: {exc}")
            return f"❌ 生成授权链接失败: {exc}"

    def _revoke_auth(self, *, open_id: str) -> str:
        """撤销本地存储的用户授权 token。"""
        try:
            from agent_service.mcp.lark_token_store import clear as clear_token
            removed = clear_token(open_id)
            return "✅ 已清除您的授权信息。" if removed else "📭 您当前没有已保存的授权信息。"
        except Exception as exc:
            return f"❌ 撤销失败: {exc}"

    def _save_and_clear(self, *, open_id: str, chat_id: str) -> str:
        """将当前飞书会话历史归档到 wiki 知识库，然后清除历史文件。

        流程：
          1. 加载历史；无历史则直接返回提示
          2. 调清洗子图（复用 feedback 同一 prompt）生成摘要
          3. 摘要写入 wiki/；失效 RAG 缓存
          4. 删除飞书历史文件
        """
        from agent_service.mcp.lark_history import load_history, clear_history

        history = load_history(open_id, chat_id)
        if not history:
            return "📭 当前没有可保存的对话历史。"

        # 拼接对话文本（与 web 端 /feedback 格式一致）
        conv_text = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')}"
            for m in history
        )
        llm_input = (
            f"用户评分：5/5\n"
            f"用户评语：飞书机器人手动归档\n\n"
            f"对话记录：\n{conv_text}"
        )

        try:
            from api import services  # lazy import，避免循环依赖
            from agent_service.graph import build_cleaning_graph
            from api.agent import _FEEDBACK_SYSTEM

            cleaner_cfg = services.load_cleaner_settings()
            if not cleaner_cfg.get("api_key"):
                return "❌ 未配置 API Key，无法清洗对话，历史未清除。"

            out = build_cleaning_graph().invoke({
                "raw_text": llm_input,
                "system_prompt": _FEEDBACK_SYSTEM,
                "cleaner_cfg": cleaner_cfg,
            })
            if out.get("error"):
                return f"❌ 清洗失败：{out['error']}，历史未清除。"

            cleaned = (out.get("cleaned_text") or "").strip()

            if cleaned:
                wiki_dir = services.get_wiki_dir()
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                safe_uid = re.sub(r"[^\w]", "_", open_id or "lark")[:20]
                filename = f"feedback_{safe_uid}_lark_{ts}_5star.txt"
                header = (
                    f"# 对话反馈摘要\n"
                    f"# 来源: 飞书机器人\n"
                    f"# 用户 open_id: {open_id or '未知'}\n"
                    f"# 会话 chat_id: {chat_id or '未知'}\n"
                    f"# 时间: {ts}\n\n"
                )
                (wiki_dir / filename).write_text(header + cleaned, encoding="utf-8")
                services.invalidate_rag()
                result_msg = f"✅ 对话已归档至知识库（{filename}），历史已清除。"
            else:
                result_msg = "📭 本轮对话无可提取的知识内容，历史已清除。"

        except Exception as exc:
            print(f"[lark_bot] _save_and_clear 失败: {exc}")
            return f"❌ 归档失败：{exc}"

        # 无论是否有内容，都删除历史
        try:
            clear_history(open_id, chat_id)
        except Exception as exc:
            print(f"[lark_bot] 历史删除失败: {exc}")

        return result_msg

    def _query(self, text: str, *, open_id: str = "", chat_id: str = "") -> str:
        """生成回复文本。

        优先路径：MCP 就绪 → ReAct Agent（具备飞书工具调用能力）
        降级路径：MCP 未就绪 → RAG QA 图（纯知识库问答）

        两条路径均通过 detect_skill() 注入专属 skill 系统提示。
        """
        # lazy import 避免循环依赖（pitfall #13）
        from api import services
        from agent_service.skill_loader import detect_skill, build_skill_table

        chat_cfg = services.load_chat_settings()
        if not chat_cfg.get("api_key"):
            print("[lark_bot] chat API key 未配置，跳过回复")
            return ""

        # ── Skill 检测（两条路径共用） ────────────────────────────────────────
        skill = detect_skill(text)
        skill_prompt: str = skill.system_prompt if skill else ""
        if skill:
            print(f"[lark_bot] 命中 skill: {skill.name}")

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
                extra_tools = []
                cfg = _load_cfg()
                app_id     = cfg.get("app_id", "")
                app_secret = cfg.get("app_secret", "")

                # 注入 tenant 级工具（app token，无需用户授权）
                try:
                    from agent_service.mcp.lark_tenant_tools import build_tenant_tools
                    tenant_tools = build_tenant_tools(app_id, app_secret)
                    extra_tools.extend(tenant_tools)
                    print(f"[lark_bot] 注入 tenant 工具: {[t.name for t in tenant_tools]}")
                except Exception as exc:
                    print(f"[lark_bot] tenant 工具加载失败（不影响基础功能）: {exc}")

                # 若用户已完成 OAuth 授权，额外注入用户级工具
                if open_id:
                    try:
                        from agent_service.mcp.lark_token_store import get_valid_token
                        user_token = get_valid_token(open_id, app_id, app_secret)
                        if user_token:
                            from agent_service.mcp.lark_user_tools import build_user_tools
                            extra_tools.extend(build_user_tools(user_token))
                    except Exception as exc:
                        print(f"[lark_bot] 用户工具加载失败（不影响基础功能）: {exc}")

                return mcp_manager.run_agent_sync(
                    text, chat_cfg,
                    history=history,
                    extra_tools=extra_tools,
                    skill_system_prompt=skill_prompt or None,
                )
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

        agent_mode = services.get_agent_mode()   # 降级路径同切 ReAct（与网页端一致）
        state = {
            "query": text,
            "history": history,
            "chat_cfg": chat_cfg,
            "rag_fn": rag_fn if rag else None,
            "top_k": 5,
            "score_threshold": services.load_rag_threshold(),
            "skill_system_prompt": skill_prompt or None,
            "skill_table": build_skill_table(),   # L1 常驻注入
            "agent_mode": agent_mode,
            "max_tool_rounds": 5,
            # 不设 web_tools：飞书仅核心工具集（不含文档读取）
        }

        full_text = ""
        try:
            for event in build_qa_graph(agent_mode).stream(state, stream_mode="custom"):
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
