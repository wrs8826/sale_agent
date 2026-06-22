"""QA 主图节点：extract → retrieve → generate。
节点内通过 langgraph 的 stream writer 主动推 tool_*/token/done 事件，
api 层用 stream_mode="custom" 直接转发为 SSE。
"""
from __future__ import annotations

from typing import Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.config import get_stream_writer

from agent_service.logging_config import get_logger

from ..state import ChatState
from .prompts import (
    EXTRACT_SYSTEM,
    PLAN_INJECT_PREFIX,
    PLAN_SYSTEM,
    build_generate_system,
)

log = get_logger(__name__)


# ── 工具函数 ──────────────────────────────────────────────────────────────────
def _history_to_messages(history: List[Dict]) -> List:
    out = []
    for m in history:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "user":
            out.append(HumanMessage(content=content))
        elif role == "tool":
            # 历史里持久化的工具调用结果，以系统提示形式回放（与当轮工具结果注入口径一致，跨厂商稳）
            name = m.get("name") or "工具"
            out.append(SystemMessage(content=f"[历史工具调用 {name} 的返回]\n{content}"))
        else:
            out.append(AIMessage(content=content))
    return out


def _content_text(content) -> str:
    """ChatOpenAI 返回的 content 可能是 str 或 list[dict]，统一抽取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


def _format_context(hits: List[Dict]) -> str:
    if not hits:
        return "（暂无相关片段）"
    parts = []
    for i, h in enumerate(hits, 1):
        src = h.get("metadata", {}).get("filename", "未知来源")
        score = h.get("hybrid_score", 0.0)
        parts.append(f"[片段 {i}] 来源：{src}（相关度 {score:.3f}）\n{h['text']}")
    return "\n\n---\n\n".join(parts)


def _build_llm(chat_cfg: Dict[str, str]) -> ChatOpenAI:
    return ChatOpenAI(
        model=chat_cfg.get("model_name") or "qwen3-max",
        api_key=chat_cfg["api_key"],
        base_url=chat_cfg.get("base_url") or None,
        streaming=True,
    )


# ── 节点 ──────────────────────────────────────────────────────────────────────

def call_tools_node(state: ChatState) -> ChatState:
    """工具调用节点：让 LLM 决定是否需要调用内置工具，执行后把结果写入 tool_results。

    LLM 若认为不需要工具，直接返回文本，tool_results 保持 None，不影响后续节点。
    """
    writer = get_stream_writer()

    from agent_service.mcp.builtin_tools import BUILTIN_TOOLS, WEB_TOOLS
    tools = WEB_TOOLS if state.get("web_tools") else BUILTIN_TOOLS
    if not tools:
        return {"tool_results": None}

    llm = _build_llm(state["chat_cfg"]).bind_tools(tools)

    # 构造工具调用消息：如果有 skill 文档地图（系统提示），注入给 LLM
    # 使模型能从文档地图中选择正确的 skill_name / filename 调用 load_policy_file
    tool_msgs = []
    skill_prompt = (state.get("skill_system_prompt") or "").strip()
    if skill_prompt:
        tool_msgs.append(SystemMessage(content=skill_prompt))
    tool_msgs.append(HumanMessage(content=state["query"]))

    try:
        response = llm.invoke(tool_msgs)
    except Exception as exc:
        log.warning("[call_tools] 工具检测异常: %s", exc)
        return {"tool_results": None}

    if not getattr(response, "tool_calls", None):
        return {"tool_results": None}

    results: List[str] = []
    tool_items: List[Dict] = []   # 结构化工具记录，供 api 层持久化为对话历史里的工具消息（Phase 0）
    for tc in response.tool_calls:
        name = tc["name"]
        args = tc.get("args", {})
        writer({"type": "tool_start", "name": name})
        matched = next((t for t in tools if t.name == name), None)
        if matched is None:
            writer({"type": "tool_end", "name": name, "error": "未找到工具"})
            continue
        try:
            result = matched.invoke(args)
            results.append(f"{name}: {result}")
            tool_items.append({"name": name, "args": args, "result": str(result)})
            writer({"type": "tool_end", "name": name, "result": str(result)})
        except Exception as exc:
            tool_items.append({"name": name, "args": args, "result": f"[工具执行失败] {exc}"})
            writer({"type": "tool_end", "name": name, "error": str(exc)})

    if tool_items:
        # 内部事件：api 层据此把工具轮写入会话历史；前端忽略未知类型，不受影响
        writer({"type": "tool_turn", "items": tool_items})

    return {"tool_results": "\n".join(results) if results else None}


def extract_keywords_node(state: ChatState) -> ChatState:
    writer = get_stream_writer()
    writer({"type": "tool_start", "name": "提取关键词"})

    try:
        llm = _build_llm(state["chat_cfg"])
        msgs = [
            SystemMessage(content=EXTRACT_SYSTEM),
            *_history_to_messages(state.get("history") or []),
            HumanMessage(content=f"用户最新问题：{state['query']}"),
        ]
        resp = llm.invoke(msgs)
        keywords = _content_text(resp.content).strip() or state["query"]
    except Exception as exc:
        keywords = state["query"]
        writer({"type": "tool_end", "name": "提取关键词", "keywords": keywords,
                "warning": f"提取异常，降级为原问题: {exc}"})
        return {"keywords": keywords}

    writer({"type": "tool_end", "name": "提取关键词", "keywords": keywords})
    return {"keywords": keywords}


def retrieve_node(state: ChatState) -> ChatState:
    writer = get_stream_writer()
    keywords = state.get("keywords") or state["query"]
    writer({"type": "tool_start", "name": "检索知识库", "keywords": keywords})

    rag_fn = state.get("rag_fn")
    top_k = state.get("top_k") or 5
    hits: List[Dict] = []
    if rag_fn is not None:
        try:
            hits = rag_fn(keywords, top_k) or []
        except Exception as exc:
            log.warning("[QA] RAG 检索异常: %s", exc)
            hits = []

    log.info("RAG 检索完成：关键词=%r，命中 %d 个分块", keywords, len(hits))
    writer({"type": "tool_end", "name": "检索知识库", "count": len(hits)})
    return {"hits": hits}


def _hits_above_threshold(hits: List[Dict], threshold: float) -> bool:
    """至少有一个命中片段的分数达到阈值。"""
    if not hits:
        return False
    best = max(
        h.get("rerank_score", h.get("hybrid_score", 0.0)) for h in hits
    )
    return best >= threshold


def generate_node(state: ChatState) -> ChatState:
    writer = get_stream_writer()
    llm = _build_llm(state["chat_cfg"])
    hits = state.get("hits") or []
    threshold = state.get("score_threshold") or 0.3
    has_hits = _hits_above_threshold(hits, threshold)

    from agent_service.mcp.builtin_tools import build_tool_table, BUILTIN_TOOLS, WEB_TOOLS

    tools = WEB_TOOLS if state.get("web_tools") else BUILTIN_TOOLS
    system_prompt = build_generate_system(
        skill_prompt=(state.get("skill_system_prompt") or "").strip(),
        context=_format_context(hits) if has_hits else "",
        tool_results=(state.get("tool_results") or "").strip(),
        skill_table=(state.get("skill_table") or "").strip(),
        has_hits=has_hits,
        tool_table=build_tool_table(tools),
    )

    msgs = [
        SystemMessage(content=system_prompt),
        *_history_to_messages(state.get("history") or []),
        HumanMessage(content=state["query"]),
    ]

    full_text = ""
    try:
        for chunk in llm.stream(msgs):
            text = _content_text(chunk.content)
            if text:
                full_text += text
                writer({"type": "token", "text": text})
    except Exception as exc:
        writer({"type": "error", "message": f"生成阶段异常: {exc}"})
        return {"full_text": full_text, "error": str(exc)}

    writer({"type": "done", "full_text": full_text})
    return {"full_text": full_text}


def _msg_from_end_output(out):
    """从 on_chat_model_end 的 output 取出 AIMessage（兼容 AIMessage / LLMResult 形态）。"""
    if out is None:
        return None
    if hasattr(out, "content") or hasattr(out, "tool_calls"):
        return out
    gens = getattr(out, "generations", None)
    if gens:
        try:
            return gens[0][0].message
        except Exception:
            return None
    return None


def _forced_answer(writer, llm, system_prompt: str, history_msgs: List, query: str,
                   tool_items: List[Dict]) -> str:
    """达到工具上限后的强制收尾：不带工具、附上已获取的工具结果，流式直接作答。"""
    if tool_items:
        tool_ctx = "\n\n".join(
            f"工具 {it.get('name')}（参数 {it.get('args')}）返回：\n{it.get('result')}"
            for it in tool_items
        )
    else:
        tool_ctx = "（本轮未成功获取工具结果）"
    sys = system_prompt + (
        "\n\n──── 已达到工具调用上限 ────\n"
        "请不要再请求任何工具，直接基于以下已获取的工具结果与对话历史，完整作答：\n" + tool_ctx
    )
    msgs = [SystemMessage(content=sys), *history_msgs, HumanMessage(content=query)]
    parts: List[str] = []
    try:
        for chunk in llm.stream(msgs):   # llm 未 bind_tools → 不会再触发工具
            text = _content_text(chunk.content)
            if text:
                parts.append(text)
                writer({"type": "token", "text": text})
    except Exception as exc:
        writer({"type": "warning", "message": f"收尾作答异常: {exc}"})
    return "".join(parts)


def plan_node(state: ChatState) -> ChatState:
    """规划节点（仅 react + enable_planning）：执行前先产出一份执行方案（任务拆分）。

    - 单次 LLM 调用，输入=用户问题 + 预检索片段 + skill 表 + 工具表。
    - 流式推 plan_start / plan_token / plan_end，前端渲染成独立「执行方案」卡片。
    - 方案全文写入 state['plan']，由 agent_react_node 注入 system prompt 作执行指令。
    - 方案不写入持久化历史（仅当轮指令），不抛异常：失败则 plan 留空，react 照常执行。
    """
    writer = get_stream_writer()

    if not state.get("enable_planning"):
        return {"plan": ""}

    from agent_service.mcp.builtin_tools import build_tool_table, BUILTIN_TOOLS, WEB_TOOLS

    tools = WEB_TOOLS if state.get("web_tools") else BUILTIN_TOOLS
    hits = state.get("hits") or []
    threshold = state.get("score_threshold") or 0.3
    has_hits = _hits_above_threshold(hits, threshold)

    system_prompt = PLAN_SYSTEM.format(
        skill_table=(state.get("skill_table") or "").strip() or "（暂无已加载的知识领域）",
        tool_table=build_tool_table(tools) or "（暂无可用工具）",
        context=_format_context(hits) if has_hits else "（暂无相关片段）",
    )

    writer({"type": "plan_start"})
    plan_text = ""
    try:
        llm = _build_llm(state["chat_cfg"])
        msgs = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"用户问题：{state['query']}\n\n请给出执行方案："),
        ]
        for chunk in llm.stream(msgs):
            text = _content_text(chunk.content)
            if text:
                plan_text += text
                writer({"type": "plan_token", "text": text})
    except Exception as exc:
        # 规划失败不阻断主流程：清空方案，react 直接执行
        writer({"type": "plan_end", "plan": "", "warning": f"规划阶段异常，跳过方案: {exc}"})
        return {"plan": ""}

    writer({"type": "plan_end", "plan": plan_text})
    return {"plan": plan_text}


def agent_react_node(state: ChatState) -> ChatState:
    """ReAct 多步自主工具循环（路线 A）：模型 思考→调工具→观察 反复，直到给出最终答案。

    - 复用 langgraph 的 create_react_agent（与飞书主路径同款）。
    - RAG：命中片段作为系统提示**预注入**（grounding），不改成纯工具检索。
    - 流式：astream_events(v2) 映射回既有 SSE —— on_chat_model_stream→token、
      on_tool_start/-end→tool_*、结束→done；工具记录汇总成一个 tool_turn 供持久化。
    - 上限：max_tool_rounds（默认 5）→ recursion_limit，超限优雅收尾。
    """
    import asyncio

    from langgraph.prebuilt import create_react_agent

    from agent_service.mcp.builtin_tools import build_tool_table, BUILTIN_TOOLS, WEB_TOOLS

    writer = get_stream_writer()
    tools = WEB_TOOLS if state.get("web_tools") else BUILTIN_TOOLS
    hits = state.get("hits") or []
    threshold = state.get("score_threshold") or 0.3
    has_hits = _hits_above_threshold(hits, threshold)

    system_prompt = build_generate_system(
        skill_prompt=(state.get("skill_system_prompt") or "").strip(),
        context=_format_context(hits) if has_hits else "",
        tool_results="",   # ReAct：工具结果走循环内 ToolMessage，不预注入
        skill_table=(state.get("skill_table") or "").strip(),
        has_hits=has_hits,
        tool_table=build_tool_table(tools),
    )

    # 若上游 plan_node 产出了执行方案，注入为执行指令（不写入持久化历史，仅当轮）
    plan = (state.get("plan") or "").strip()
    if plan:
        system_prompt += PLAN_INJECT_PREFIX + plan

    llm = _build_llm(state["chat_cfg"])
    try:
        agent = create_react_agent(llm, tools, prompt=system_prompt)
    except Exception as exc:
        writer({"type": "error", "message": f"Agent 初始化失败: {exc}"})
        return {"full_text": "", "error": str(exc)}

    history_msgs = _history_to_messages(state.get("history") or [])
    msgs = [*history_msgs, HumanMessage(content=state["query"])]
    max_rounds = int(state.get("max_tool_rounds") or 5)
    recursion_limit = max_rounds * 2 + 1   # 每轮 ≈ 1 次 LLM + 1 次 tools 节点

    tool_items: List[Dict] = []
    pending: Dict[str, Dict] = {}      # run_id → {name, args}，配对 tool_start/tool_end
    run_is_tool: Dict[str, bool] = {}  # run_id → 该 LLM 轮是否在调工具（用于不外流思考）
    run_text: Dict[str, str] = {}      # run_id → 该轮流出的答案文本（兜底用）
    final_text = ""                    # 权威最终答案（无 tool_calls 的 on_chat_model_end）

    async def _run():
        nonlocal final_text
        async for ev in agent.astream_events(
            {"messages": msgs}, version="v2",
            config={"recursion_limit": recursion_limit},
        ):
            kind = ev.get("event")
            data = ev.get("data") or {}
            rid = ev.get("run_id", "")
            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if getattr(chunk, "tool_call_chunks", None):
                    run_is_tool[rid] = True   # 这一轮在调工具，文本视为思考，不外流
                if not run_is_tool.get(rid):
                    text = _content_text(getattr(chunk, "content", "")) if chunk is not None else ""
                    if text:
                        run_text[rid] = run_text.get(rid, "") + text
                        writer({"type": "token", "text": text})
            elif kind == "on_chat_model_end":
                msg = _msg_from_end_output(data.get("output"))
                if msg is not None and not getattr(msg, "tool_calls", None):
                    txt = _content_text(getattr(msg, "content", ""))
                    if txt.strip():
                        final_text = txt   # 终轮答案（无 tool_calls）
            elif kind == "on_tool_start":
                name = ev.get("name", "")
                args = data.get("input")
                pending[rid] = {"name": name, "args": args}
                writer({"type": "tool_start", "name": name})
            elif kind == "on_tool_end":
                info = pending.pop(rid, {"name": ev.get("name", ""), "args": None})
                out = data.get("output")
                result = getattr(out, "content", None)
                result = str(result if result is not None else out)
                tool_items.append({"name": info["name"], "args": info.get("args"), "result": result})
                # ToolMessage.status=='error' 时明确标失败，供前端清单渲染 [❌]
                end_ev = {"type": "tool_end", "name": info["name"], "result": result}
                if getattr(out, "status", None) == "error":
                    end_ev["error"] = result
                writer(end_ev)

    hit_limit = False
    try:
        asyncio.run(_run())
    except Exception as exc:
        if "Recursion" in type(exc).__name__:
            hit_limit = True
        else:
            writer({"type": "error", "message": f"Agent 运行异常: {exc}"})
            return {"full_text": final_text, "error": str(exc)}

    if hit_limit:
        writer({"type": "warning",
                "message": f"已达到最大工具调用轮数（{max_rounds}），正在基于已获取信息作答…"})
        final_text = _forced_answer(writer, llm, system_prompt, history_msgs, state["query"], tool_items)

    # 兜底：未从 on_chat_model_end 拿到终轮答案时，取最后一个"非工具轮"流出的文本
    if not final_text.strip():
        final_text = next(
            (t for rid_, t in reversed(list(run_text.items())) if not run_is_tool.get(rid_) and t.strip()),
            "",
        )
    if not final_text.strip():
        final_text = "（未能生成回答，请重述问题或缩小范围）"
        writer({"type": "token", "text": final_text})

    if tool_items:
        writer({"type": "tool_turn", "items": tool_items})
    writer({"type": "done", "full_text": final_text})
    return {"full_text": final_text}
