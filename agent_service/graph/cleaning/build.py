"""组装清洗子图。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..state import CleaningState
from .edges import after_clean, after_read, route_input
from .nodes import clean_node, read_file_node


def build_cleaning_graph():
    """编译并返回清洗子图。

    Graph layout:
        START ──route_input──┬─► read_file ──after_read──┬─► clean ── END
                             │                           └─► END (error)
                             └─► clean (raw_text 已填)
                             └─► END   (raw_text & file_path 都缺)
    """
    g = StateGraph(CleaningState)
    g.add_node("read_file", read_file_node)
    g.add_node("clean", clean_node)

    g.add_conditional_edges(
        START,
        route_input,
        {"read_file": "read_file", "clean": "clean", "end": END},
    )
    g.add_conditional_edges(
        "read_file",
        after_read,
        {"clean": "clean", "end": END},
    )
    g.add_conditional_edges(
        "clean",
        after_clean,
        {"end": END},
    )

    return g.compile()
