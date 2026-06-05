"""QA 主图节点：extract → retrieve → generate。
节点内通过 langgraph 的 stream writer 主动推 tool_*/token/done 事件，
api 层用 stream_mode="custom" 直接转发为 SSE。
"""
from __future__ import annotations

from typing import Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.config import get_stream_writer

from ..state import ChatState
from .prompts import EXTRACT_SYSTEM, GENERATE_FALLBACK_SYSTEM, GENERATE_SYSTEM, TOOL_RESULTS_PREFIX


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

    from agent_service.mcp.builtin_tools import BUILTIN_TOOLS
    if not BUILTIN_TOOLS:
        return {"tool_results": None}

    llm = _build_llm(state["chat_cfg"]).bind_tools(BUILTIN_TOOLS)

    try:
        response = llm.invoke([HumanMessage(content=state["query"])])
    except Exception as exc:
        print(f"[call_tools] 工具检测异常: {exc}")
        return {"tool_results": None}

    if not getattr(response, "tool_calls", None):
        return {"tool_results": None}

    results: List[str] = []
    for tc in response.tool_calls:
        name = tc["name"]
        args = tc.get("args", {})
        writer({"type": "tool_start", "name": name})
        matched = next((t for t in BUILTIN_TOOLS if t.name == name), None)
        if matched is None:
            writer({"type": "tool_end", "name": name, "error": "未找到工具"})
            continue
        try:
            result = matched.invoke(args)
            results.append(f"{name}: {result}")
            writer({"type": "tool_end", "name": name, "result": str(result)})
        except Exception as exc:
            writer({"type": "tool_end", "name": name, "error": str(exc)})

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
            print(f"[QA] RAG 检索异常: {exc}")
            hits = []

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
    skill_prompt = (state.get("skill_system_prompt") or "").strip()
    tool_results = (state.get("tool_results") or "").strip()

    if _hits_above_threshold(hits, threshold):
        ctx = _format_context(hits)
        if skill_prompt:
            system_prompt = (
                skill_prompt
                + "\n\n──── 检索片段 ────\n"
                + ctx
                + "\n──── 片段结束 ────"
            )
        else:
            system_prompt = GENERATE_SYSTEM.format(context=ctx)
    else:
        system_prompt = skill_prompt or GENERATE_FALLBACK_SYSTEM

    # 工具结果追加到系统提示末尾（不覆盖 RAG 上下文）
    if tool_results:
        system_prompt += TOOL_RESULTS_PREFIX + tool_results

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
