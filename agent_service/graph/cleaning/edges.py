"""清洗子图边路由：根据 state 内容决定下一节点。"""
from __future__ import annotations

from ..state import CleaningState


def route_input(state: CleaningState) -> str:
    """入口分支：raw_text 已填则跳过 read_file 直接 clean。"""
    if state.get("error"):
        return "end"
    if state.get("raw_text"):
        return "clean"
    if state.get("file_path"):
        return "read_file"
    return "end"  # 既无 raw_text 又无 file_path —— 调用方传参错误，直接结束


def after_read(state: CleaningState) -> str:
    if state.get("error"):
        return "end"
    return "clean"


def after_clean(_: CleaningState) -> str:
    return "end"
