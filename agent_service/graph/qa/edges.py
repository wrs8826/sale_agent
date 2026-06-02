"""QA 主图边路由。"""
from __future__ import annotations

from ..state import ChatState


def after_extract(state: ChatState) -> str:
    """无 rag_fn 时跳过检索直接生成（降级，不推荐但保留）。"""
    if state.get("rag_fn") is None:
        return "generate"
    return "retrieve"
