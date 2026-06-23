"""清洗子图节点。
所有 LLM 调用 / 文件 IO 集中在这里；存储留给 api 层。
"""
from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from ..state import CleaningState


def read_file_node(state: CleaningState) -> CleaningState:
    """从 file_path 读磁盘填到 raw_text。仅当调用方未直接提供 raw_text 时触发。"""
    fp = state.get("file_path") or ""
    if not fp:
        return {"error": "read_file: 未提供 file_path"}
    try:
        from agent_service.text_utils import read_text_smart
        raw = read_text_smart(fp)  # 自动识别 UTF-8/GBK 等编码，避免中文乱码
        if not raw:
            return {"error": "read_file: 文件为空"}
        return {"raw_text": raw}
    except Exception as exc:
        return {"error": f"read_file: {exc}"}


def clean_node(state: CleaningState) -> CleaningState:
    """调 LLM 用 system_prompt 清洗 raw_text，产 cleaned_text。"""
    raw = state.get("raw_text") or ""
    if not raw:
        return {"cleaned_text": ""}

    cfg = state.get("cleaner_cfg") or {}
    if not cfg.get("api_key"):
        return {"error": "clean: cleaner_cfg.api_key 为空"}

    sys_prompt = state.get("system_prompt") or ""
    if not sys_prompt:
        return {"error": "clean: 未提供 system_prompt"}

    try:
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url") or None)
        resp = client.chat.completions.create(
            model=cfg.get("model_name") or "qwen3-max",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": raw},
            ],
            extra_body={"enable_thinking": False},
        )
        cleaned = (resp.choices[0].message.content or "").strip()
        return {"cleaned_text": cleaned}
    except Exception as exc:
        return {"error": f"clean: {exc}"}
