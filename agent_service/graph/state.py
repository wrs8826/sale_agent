"""图状态定义。两张图（cleaning / qa）各持一套状态，互不耦合。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, TypedDict


class CleaningState(TypedDict, total=False):
    """清洗子图共享状态。

    调用方至少提供 system_prompt + cleaner_cfg + (file_path 或 raw_text 之一)。
    """
    # ── 输入 ────────────────────────────────────────────────────────────────
    file_path: str              # 可选：走文件路径时由 read_file 节点读盘填充 raw_text
    raw_text: str               # 可选：调用方已拼好原文则直接传，跳过 read_file
    system_prompt: str          # 必填：决定清洗口径（销售对话清洗 / 反馈摘要 …）
    cleaner_cfg: Dict[str, str] # 必填：{api_key, base_url, model_name}
    # ── 节点产出 ────────────────────────────────────────────────────────────
    cleaned_text: str           # clean_node 写
    # ── 错误 ────────────────────────────────────────────────────────────────
    error: Optional[str]


class ChatState(TypedDict, total=False):
    """QA 主图共享状态。"""
    # ── 输入 ────────────────────────────────────────────────────────────────
    query: str                  # 本轮用户问题
    history: List[Dict]         # [{role, content}, ...]
    chat_cfg: Dict[str, str]    # {api_key, base_url, model_name}
    rag_fn: Optional[Callable]  # (query, k) → List[hit]；None 时跳过检索
    top_k: int                  # 检索片段数
    score_threshold: float      # 低于该分时忽略命中，改用会话上下文兜底
    skill_system_prompt: Optional[str]  # skill 匹配时覆盖默认生成提示词
    tool_results: Optional[str]         # call_tools_node 执行工具后的结果摘要
    # ── 节点产出 ────────────────────────────────────────────────────────────
    keywords: str               # extract 节点写
    hits: List[Dict[str, Any]]  # retrieve 节点写
    full_text: str              # generate 节点写
    # ── 错误 ────────────────────────────────────────────────────────────────
    error: Optional[str]
