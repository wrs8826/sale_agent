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

正常情况下由 lark_bot 的 token 占比检测触发 split_for_reset：只保留最开始
HEAD_KEEP_TURNS 轮 + 最近 TAIL_KEEP_TURNS 轮，中间段由调用方归档后丢弃。
append_turn 自身仅做 _HARD_CAP_TURNS 兜底截断（防止检测失效时无限增长）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from agent_service import LARK_CONVERSATIONS_DIR

# 触发 token 占比重置后：保留的头部锚点轮数 + 尾部近期轮数
HEAD_KEEP_TURNS: int = 2
TAIL_KEEP_TURNS: int = 5

# 兜底硬上限（1轮 = user + assistant 各一条）：仅在 token 占比检测因故未触发时
# 防止历史无限增长，正常情况下 split_for_reset 会在远早于此之前完成重置。
_HARD_CAP_TURNS: int = 30


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
    """向指定会话追加一轮对话，并自动截断超出 _HARD_CAP_TURNS 的旧消息。

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

    # 兜底截断：只保留最近 _HARD_CAP_TURNS 轮 = _HARD_CAP_TURNS * 2 条
    max_msgs = _HARD_CAP_TURNS * 2
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


def split_for_reset(
    open_id: str,
    chat_id: str,
    head_turns: int = HEAD_KEEP_TURNS,
    tail_turns: int = TAIL_KEEP_TURNS,
) -> Tuple[Optional[List[Dict[str, str]]], int]:
    """若总轮数超过 head_turns + tail_turns，丢弃中间段、只保留首尾，原子写回。

    每轮固定为 1 条 user + 1 条 assistant（append_turn 的写入方式保证），
    故轮边界按「每 2 条消息」切分，无需像网页端那样按 role 扫描定位。

    返回 (被丢弃的中间轮次 [{role, content}, ...], 重置后保留的消息条数)。
    若轮数不足以丢弃任何内容、文件不存在或解析失败，返回 (None, 0)——
    调用方应视为「无需重置」，不算错误。
    """
    fp = _file_path(open_id, chat_id)
    if not fp.exists():
        return None, 0
    try:
        data: Dict[str, Any] = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None, 0

    messages: List[Dict[str, Any]] = data.get("messages", [])
    total_turns = len(messages) // 2
    if total_turns <= head_turns + tail_turns:
        return None, 0

    head_end = head_turns * 2
    tail_start = (total_turns - tail_turns) * 2
    middle = messages[head_end:tail_start]
    kept = messages[:head_end] + messages[tail_start:]

    data["messages"] = kept
    data["updated_at"] = _now_iso()

    tmp = fp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)

    middle_view = [{"role": m["role"], "content": m["content"]} for m in middle]
    return middle_view, len(kept)
