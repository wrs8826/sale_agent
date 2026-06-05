"""组装 QA 主图。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..state import ChatState
from .edges import after_extract
from .nodes import call_tools_node, extract_keywords_node, generate_node, retrieve_node


def build_qa_graph():
    """编译并返回 QA 主图。

    Graph layout:
        START → call_tools → extract_keywords ──after_extract──┬─► retrieve → generate → END
                                                               └─► generate → END

    call_tools 在提取关键词之前运行，若 LLM 判断需要工具则执行并把结果写入
    tool_results；无工具需求时直接透传，不影响后续节点。
    """
    g = StateGraph(ChatState)
    g.add_node("call_tools", call_tools_node)
    g.add_node("extract_keywords", extract_keywords_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)

    g.add_edge(START, "call_tools")
    g.add_edge("call_tools", "extract_keywords")
    g.add_conditional_edges(
        "extract_keywords",
        after_extract,
        {"retrieve": "retrieve", "generate": "generate"},
    )
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)

    return g.compile()
