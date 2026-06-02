"""组装 QA 主图。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..state import ChatState
from .edges import after_extract
from .nodes import extract_keywords_node, generate_node, retrieve_node


def build_qa_graph():
    """编译并返回 QA 主图。

    Graph layout:
        START → extract_keywords ──after_extract──┬─► retrieve → generate → END
                                                  └─► generate → END
    """
    g = StateGraph(ChatState)
    g.add_node("extract_keywords", extract_keywords_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)

    g.add_edge(START, "extract_keywords")
    g.add_conditional_edges(
        "extract_keywords",
        after_extract,
        {"retrieve": "retrieve", "generate": "generate"},
    )
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)

    return g.compile()
