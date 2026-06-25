"""MCP 客户端管理器 — 在后台 asyncio 线程中持久持有飞书 MCP 上下文。

配置文件：agent_service/mcp/lark_mcp.json（mcpServers 格式）

使用方式（模块级单例）：
    from agent_service.mcp.mcp_manager import mcp_manager

    mcp_manager.on_status_change(callback)  # 注册状态变更回调
    mcp_manager.start()                     # app 启动时调用一次
    status = mcp_manager.get_status()       # 查当前状态
    reply  = mcp_manager.run_agent_sync(message, chat_cfg)  # 同步调用
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# lark_mcp.json 与本文件同目录
_CONFIG_PATH = Path(__file__).resolve().parent / "lark_mcp.json"

STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_ERROR = "error"


class MCPManager:
    def __init__(self) -> None:
        self._state: str = STATE_IDLE
        self._tools: list = []
        self._error: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._client: Optional[Any] = None
        self._started = False
        self._lock = threading.Lock()

    # ── 外部接口 ──────────────────────────────────────────────────────────────

    def on_status_change(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._callbacks.append(callback)

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "tools": [t.name for t in self._tools],
            "count": len(self._tools),
            "error": self._error,
        }

    def get_tools(self) -> list:
        return list(self._tools)

    def start(self) -> None:
        """启动后台 asyncio 线程（幂等）。"""
        with self._lock:
            if self._started:
                return
            self._started = True

        self._state = STATE_LOADING
        self._notify()

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._thread_main, name="mcp-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._shutdown_event:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)

    def run_agent_sync(
        self,
        message: str,
        chat_cfg: Dict[str, str],
        history: Optional[List[Dict]] = None,
        extra_tools: Optional[List] = None,
        skill_system_prompt: Optional[str] = None,
        timeout: int = 120,
    ) -> str:
        """同步调用飞书 Agent，阻塞等待结果。

        chat_cfg             来自 services.load_chat_settings()。
        history              [{role, content}, ...] 多轮历史，可选。
        extra_tools          额外注入的 LangChain 工具（如用户级工具），与 MCP 工具合并。
        skill_system_prompt  命中 skill 时的专属系统提示，拼在飞书工具说明之前。
        """
        if self._state != STATE_READY:
            raise RuntimeError(f"飞书 MCP 未就绪，当前状态: {self._state}（{self._error or ''}）")
        future = asyncio.run_coroutine_threadsafe(
            self._invoke_agent(
                message, chat_cfg, history or [], extra_tools or [],
                skill_system_prompt=skill_system_prompt,
            ),
            self._loop,
        )
        return future.result(timeout=timeout)

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    def _notify(self) -> None:
        status = self.get_status()
        for cb in self._callbacks:
            try:
                cb(status)
            except Exception as exc:
                print(f"[mcp_manager] 回调异常: {exc}")

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_main())

    async def _async_main(self) -> None:
        self._shutdown_event = asyncio.Event()

        if not _CONFIG_PATH.exists():
            self._state = STATE_ERROR
            self._error = f"配置文件不存在: {_CONFIG_PATH}"
            self._notify()
            return

        try:
            raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            # 支持 { "mcpServers": {...} } 和直接 { "server": {...} } 两种格式
            config = raw.get("mcpServers", raw)
        except Exception as exc:
            self._state = STATE_ERROR
            self._error = f"解析 lark_mcp.json 失败: {exc}"
            self._notify()
            return

        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError:
            self._state = STATE_ERROR
            self._error = "缺少依赖：pip install langchain-mcp-adapters"
            self._notify()
            return

        try:
            client = MultiServerMCPClient(config)
            tools = await client.get_tools()
            self._tools = tools
            self._client = client          # 持有引用，防止 GC 断开连接
            self._state = STATE_READY
            self._error = None
            self._notify()
            print(f"[mcp_manager] 飞书工具加载成功（{len(tools)} 个）: {[t.name for t in tools]}")
            await self._shutdown_event.wait()
        except Exception as exc:
            self._state = STATE_ERROR
            self._error = str(exc)
            self._notify()
            print(f"[mcp_manager] MCP 初始化失败: {exc}")

    async def _invoke_agent(
        self,
        message: str,
        chat_cfg: Dict[str, str],
        history: List[Dict],
        extra_tools: List,
        skill_system_prompt: Optional[str] = None,
    ) -> str:
        from langchain_openai import ChatOpenAI
        from langgraph.prebuilt import create_react_agent
        from langchain_core.messages import SystemMessage

        from agent_service.graph.qa.nodes import llm_tuning_kwargs

        # 推理模式开关 + temperature 与网页端 graph/qa/nodes._build_llm 共用同一逻辑：
        # 思考模式下 temperature 不生效，故仅在关闭推理时由 helper 设 temperature=0。
        llm = ChatOpenAI(
            model=chat_cfg["model_name"],
            api_key=chat_cfg["api_key"],
            base_url=chat_cfg["base_url"] or None,
            **llm_tuning_kwargs(chat_cfg),
        )
        # 合并 MCP 工具 + 额外注入的用户级工具
        all_tools = list(self._tools) + list(extra_tools)

        # 构建 system prompt：告知 LLM 可用工具，防止其凭训练知识直接拒绝
        tool_names = [t.name for t in all_tools]
        contact_tools = [n for n in tool_names if "contact" in n or "department" in n]
        contact_tool_list = ", ".join(contact_tools) if contact_tools else "（无）"
        feishu_tool_content = (
            "你是一个飞书智能助手，可以调用工具完成用户请求。\n\n"
            "【通讯录查询规则 - 非常重要】\n"
            "查询联系人、通讯录成员、部门成员时，必须且只能使用以下工具：\n"
            f"  {contact_tool_list}\n"
            "严禁使用 contact_v3_user_batchGetId 查询通讯录列表，该工具仅用于已知 ID 时的反查。\n\n"
            "【通用原则】\n"
            "- 必须先调用工具，不要凭训练知识直接回答或拒绝。\n"
            "- 工具调用失败时，返回工具的原始错误信息，不要自行判断权限问题。\n"
            "- 用中文回复用户。"
        )
        # skill 提示词优先，拼在飞书工具说明之前
        if skill_system_prompt:
            system_content = skill_system_prompt.strip() + "\n\n---\n\n" + feishu_tool_content
        else:
            system_content = feishu_tool_content

        agent = create_react_agent(llm, all_tools, prompt=system_content)
        # 将历史消息转为 (role, content) 列表，末尾追加本轮问题
        messages = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in history
        ] + [{"role": "user", "content": message}]
        response = await agent.ainvoke({"messages": messages})
        return response["messages"][-1].content


mcp_manager = MCPManager()
