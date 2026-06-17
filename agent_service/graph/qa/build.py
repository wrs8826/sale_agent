"""组装 QA 主图。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..state import ChatState
from .edges import after_extract
from .nodes import (
    agent_react_node,
    call_tools_node,
    extract_keywords_node,
    generate_node,
    retrieve_node,
)


def build_qa_graph(agent_mode: str = "single"):
    """编译并返回 QA 主图。`agent_mode` 由 feature flag 决定，两套形态：

    single（默认）：
        START → call_tools → extract_keywords ──┬─► retrieve → generate → END
                                                 └─► generate → END
        call_tools 单趟一次工具调用，结果注入 generate。

    react（多步自主工具循环）：
        START → extract_keywords ──┬─► retrieve → agent_react → END
                                   └─► agent_react → END
        先预检索作 grounding，再由 agent_react（create_react_agent）多步循环调工具后作答。
    """
    g = StateGraph(ChatState)
    g.add_node("extract_keywords", extract_keywords_node)
    g.add_node("retrieve", retrieve_node)

    if agent_mode == "react":
        g.add_node("agent_react", agent_react_node)
        g.add_edge(START, "extract_keywords")
        g.add_conditional_edges(
            "extract_keywords",
            after_extract,
            {"retrieve": "retrieve", "generate": "agent_react"},
        )
        g.add_edge("retrieve", "agent_react")
        g.add_edge("agent_react", END)
    else:
        g.add_node("call_tools", call_tools_node)
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
