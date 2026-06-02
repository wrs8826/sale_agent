"""飞书机器人对话历史持久化。

存储路径：agent_service/lark_conversations/<safe_key>.json
唯一标识：open_id（飞书用户 ID）+ chat_id（会话 ID）组合

文件格式：
{
  "open_id":    "ou_xxx",
  "chat_id":    "oc_xxx",
  "updated_at": "ISO 8601 UTC",
  "messages": [
    {"role": "user",      "content": "...", "ts": "ISO 8601"},
    {"role": "assistant", "content": "...", "ts": "ISO 8601"},
    ...
  ]
}

只保留最近 MAX_TURNS 轮（每轮 = 1 user + 1 assistant），超出时截断旧消息，
防止 token 超限。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from agent_service import LARK_CONVERSATIONS_DIR

# 每个会话最多保留的轮数（1轮 = user + assistant 各一条）
MAX_TURNS: int = 10


def _ensure_dir() -> None:
    LARK_CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_key(open_id: str, chat_id: str) -> str:
    """把 open_id 和 chat_id 拼成安全文件名（只保留字母数字和 _-）。"""
    def sanitize(s: str) -> str:
        return re.sub(r"[^\w\-]", "_", s or "unknown")[:64]
    return f"{sanitize(open_id)}__{sanitize(chat_id)}"


def _file_path(open_id: str, chat_id: str) -> Path:
    return LARK_CONVERSATIONS_DIR / f"{_safe_key(open_id, chat_id)}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 公开 API ──────────────────────────────────────────────────────────────────

def load_history(open_id: str, chat_id: str) -> List[Dict[str, str]]:
    """读取指定会话的历史消息列表（[{role, content}, ...]），供 QA 图使用。

    若文件不存在或解析失败，返回空列表。
    """
    fp = _file_path(open_id, chat_id)
    if not fp.exists():
        return []
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        messages = data.get("messages", [])
        # 只返回 role/content，去掉 ts 等内部字段
        return [{"role": m["role"], "content": m["content"]} for m in messages]
    except Exception:
        return []


def append_turn(
    open_id: str,
    chat_id: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """向指定会话追加一轮对话，并自动截断超出 MAX_TURNS 的旧消息。

    写入采用 tmp → replace 原子操作，防止文件半写损坏。
    """
    _ensure_dir()
    fp = _file_path(open_id, chat_id)

    # 读取已有数据
    if fp.exists():
        try:
            data: Dict[str, Any] = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    messages: List[Dict[str, Any]] = data.get("messages", [])
    ts = _now_iso()
    messages.append({"role": "user",      "content": user_text,      "ts": ts})
    messages.append({"role": "assistant", "content": assistant_text, "ts": ts})

    # 截断：只保留最近 MAX_TURNS 轮 = MAX_TURNS * 2 条
    max_msgs = MAX_TURNS * 2
    if len(messages) > max_msgs:
        messages = messages[-max_msgs:]

    data.update({
        "open_id":    open_id,
        "chat_id":    chat_id,
        "updated_at": ts,
        "messages":   messages,
    })

    # 原子写
    tmp = fp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)


def clear_history(open_id: str, chat_id: str) -> bool:
    """清除指定会话的历史（返回 True 表示文件存在并已删除）。"""
    fp = _file_path(open_id, chat_id)
    if fp.exists():
        fp.unlink()
        return True
    return False
