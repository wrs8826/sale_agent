"""会话持久化蓝图 + 两级历史压缩（支持按用户隔离）。

存储结构：
    agent_service/conversations/<user_id>/<uuid>.json

每个会话 JSON 结构：
    {
        "id":         "<uuid4 hex>",
        "user_id":    <int>,           ← 归属用户（MySQL users.id）
        "title":      "首条用户消息截取的标题（可改名）",
        "created_at": "ISO-8601",
        "updated_at": "ISO-8601",
        "summary":    "已压缩部分的事实摘要（空表示从未压缩）",
        "compact_at": <int>,           ← messages 数组中第一条未压缩消息的下标
        "messages":   [ {role, content, ts}, ... ]
    }

提供路由：
    GET    /conversations                       列表（普通用户只看自己的，admin 看全部）
    POST   /conversations                       新建空会话（绑定当前 session user_id）
    GET    /conversations/<id>                  取完整会话（校验归属）
    PATCH  /conversations/<id>                  重命名（校验归属）
    DELETE /conversations/<id>                  删除（校验归属）
    POST   /conversations/<id>/compact          手动触发压缩（校验归属）

向前兼容：CONVERSATIONS_DIR 根目录下的老 *.json 文件（无 user_id）
仅 admin 可见，普通用户看不到。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from flask import Blueprint, jsonify, request, session

from agent_service import CONVERSATIONS_DIR
from agent_service.graph import build_cleaning_graph
from . import conv_stats

bp = Blueprint("conversations", __name__)


# ── 压缩相关常量 ─────────────────────────────────────────────────────────────
MAX_CONTEXT_TOKENS = 32000
COMPACT_THRESHOLD = 0.80
LEVEL1_KEEP_TAIL_PAIRS = 4
LEVEL2_KEEP_TAIL_PAIRS = 2

COMPACT_SYSTEM = (
    "你是对话历史压缩助手。请把下方对话整理为简明扼要的事实摘要：\n"
    "- 保留实质信息（项目、需求、决策、人物、数字、技术要点、未决问题等）\n"
    "- 忽略寒暄、重复内容、闲聊\n"
    "- 若输入开头含「[历史摘要]」段（来自前一次压缩），把它一并融合到新摘要中\n"
    "- 输出 3-8 句简明陈述，用第三人称（「用户提到…」「助手回答…」），"
    "不加引导词、不加标题、不加解释\n"
    "- 总长度尽量控制在 600 字内"
)


# ── 路径工具 ──────────────────────────────────────────────────────────────────
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_DEFAULT_TITLE = "新对话"
_TITLE_MAX = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_id(cid: str) -> Optional[str]:
    if cid and _ID_RE.match(cid):
        return cid
    return None


def _user_dir(user_id: int) -> Path:
    """用户专属子目录。"""
    return CONVERSATIONS_DIR / str(user_id)


def _path(cid: str, user_id: int) -> Path:
    """返回会话文件的绝对路径。"""
    return _user_dir(user_id) / f"{cid}.json"


# ── CRUD 基础函数 ─────────────────────────────────────────────────────────────

def load_conversation(cid: str, user_id: int) -> Optional[Dict]:
    """从指定用户目录加载会话。找不到返回 None。"""
    safe = _safe_id(cid)
    if not safe:
        return None
    fp = _path(safe, user_id)
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_conversation(cid: str) -> Optional[Dict]:
    """跨所有用户目录搜索会话（仅供 admin 或内部使用）。
    先查子目录，再查根目录（老格式兼容）。
    """
    safe = _safe_id(cid)
    if not safe:
        return None
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    # 先搜各用户子目录
    for user_dir in CONVERSATIONS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        fp = user_dir / f"{safe}.json"
        if fp.is_file():
            try:
                return json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
    # 再搜根目录（向前兼容老格式）
    fp = CONVERSATIONS_DIR / f"{safe}.json"
    if fp.is_file():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def save_conversation(conv: Dict) -> None:
    """保存到 user_id 子目录，原子写（tmp → replace）。"""
    user_id = conv.get("user_id")
    if user_id is None:
        raise ValueError("save_conversation: conv 缺少 user_id 字段")
    target_dir = _user_dir(int(user_id))
    target_dir.mkdir(parents=True, exist_ok=True)
    fp = target_dir / f"{conv['id']}.json"
    tmp = fp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)


def new_id() -> str:
    return uuid.uuid4().hex


def make_title_from_msg(text: str) -> str:
    text = (text or "").strip().splitlines()[0] if text else ""
    text = text.strip()
    if not text:
        return _DEFAULT_TITLE
    if len(text) > _TITLE_MAX:
        text = text[: _TITLE_MAX - 1] + "…"
    return text


# ── 业务函数（供 agent.py 调用，需显式传入 user_id）────────────────────────

def append_turn(cid: str, user_id: int, user_text: str, assistant_text: str) -> Optional[Dict]:
    """追加一轮对话（user + assistant）。返回更新后的 conv 或 None。"""
    conv = load_conversation(cid, user_id)
    if conv is None:
        return None
    ts = _now()
    conv["messages"].append({"role": "user", "content": user_text, "ts": ts})
    conv["messages"].append({"role": "assistant", "content": assistant_text, "ts": ts})
    conv["updated_at"] = ts
    if conv.get("title") in (None, "", _DEFAULT_TITLE):
        conv["title"] = make_title_from_msg(user_text)
    save_conversation(conv)
    return conv


def get_history(cid: str, user_id: int) -> List[Dict[str, str]]:
    """给 /agent/chat 用：返回 [{role, content}, ...]。
    若已压缩，在历史前插入 system 摘要消息。
    """
    conv = load_conversation(cid, user_id)
    if conv is None:
        return []
    history: List[Dict[str, str]] = []
    summary = (conv.get("summary") or "").strip()
    if summary:
        history.append({"role": "system", "content": f"[历史摘要]\n{summary}"})
    compact_at = int(conv.get("compact_at", 0) or 0)
    for m in conv.get("messages", [])[compact_at:]:
        history.append({"role": m["role"], "content": m["content"]})
    return history


# ── token 估算 ────────────────────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    non_cjk = len(text) - cjk
    return cjk + int(non_cjk * 0.75) + 4


def estimate_history_tokens(history: List[Dict[str, str]]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in history)


# ── 压缩 ──────────────────────────────────────────────────────────────────────
def _format_for_compaction(prior_summary: str, messages: List[Dict]) -> str:
    parts: List[str] = []
    if prior_summary:
        parts.append(f"[历史摘要]\n{prior_summary}\n")
    for m in messages:
        role = "用户" if m.get("role") == "user" else "助手"
        parts.append(f"{role}：{m.get('content','')}")
    return "\n".join(parts)


def compact_conversation(
    cid: str,
    user_id: int,
    cleaner_cfg: Dict[str, str],
    keep_tail_pairs: int,
) -> Dict:
    """对单个会话执行压缩。

    返回：
      {"ok": True, "summary": str, "compacted_count": int, "kept_count": int}
      {"unchanged": True, "reason": str}
      {"error": str}
    """
    conv = load_conversation(cid, user_id)
    if conv is None:
        return {"error": "会话不存在"}

    messages = conv.get("messages", [])
    compact_at = int(conv.get("compact_at", 0) or 0)
    prior_summary = (conv.get("summary") or "").strip()

    tail_size = keep_tail_pairs * 2
    tail_start = max(0, len(messages) - tail_size)

    if tail_start <= compact_at:
        return {"unchanged": True, "reason": "对话过短，无需压缩"}

    to_compact = messages[compact_at:tail_start]
    raw = _format_for_compaction(prior_summary, to_compact)
    if not raw.strip():
        return {"unchanged": True, "reason": "无新内容可压缩"}

    if not cleaner_cfg.get("api_key"):
        return {"error": "未配置 API Key（chat / cleaner 段任一即可）"}

    out = build_cleaning_graph().invoke({
        "raw_text": raw,
        "system_prompt": COMPACT_SYSTEM,
        "cleaner_cfg": cleaner_cfg,
    })
    if out.get("error"):
        return {"error": f"压缩失败：{out['error']}"}

    new_summary = (out.get("cleaned_text") or "").strip()
    if not new_summary:
        return {"error": "压缩失败：模型返回空内容"}

    conv["summary"] = new_summary
    conv["compact_at"] = tail_start
    conv["updated_at"] = _now()
    save_conversation(conv)

    return {
        "ok": True,
        "summary": new_summary,
        "compacted_count": tail_start - compact_at,
        "kept_count": len(messages) - tail_start,
    }


def _summary(conv: Dict) -> Dict:
    msgs = conv.get("messages") or []
    return {
        "id": conv["id"],
        "user_id": conv.get("user_id"),
        "title": conv.get("title") or _DEFAULT_TITLE,
        "created_at": conv.get("created_at"),
        "updated_at": conv.get("updated_at"),
        "message_count": len(msgs),
        "has_summary": bool((conv.get("summary") or "").strip()),
        "compact_at": int(conv.get("compact_at", 0) or 0),
    }


# ── 鉴权辅助 ──────────────────────────────────────────────────────────────────
def _current_user_id() -> Optional[int]:
    uid = session.get("user_id")
    return int(uid) if uid is not None else None


def _is_admin() -> bool:
    return session.get("role") == "admin"


def _check_ownership(conv: Dict) -> bool:
    """普通用户只能操作自己的会话；admin 可以操作任何会话。"""
    if _is_admin():
        return True
    uid = _current_user_id()
    return uid is not None and conv.get("user_id") == uid


# ── 路由 ──────────────────────────────────────────────────────────────────────
@bp.route("/conversations", methods=["GET"])
def list_conversations():
    uid = _current_user_id()
    if uid is None:
        return jsonify({"error": "未登录"}), 401

    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out = []

    if _is_admin():
        # admin：遍历所有用户子目录 + 根目录（老格式）
        dirs_to_scan: List[Path] = [CONVERSATIONS_DIR]
        for sub in CONVERSATIONS_DIR.iterdir():
            if sub.is_dir():
                dirs_to_scan.append(sub)
    else:
        # 普通用户：只扫自己的子目录
        dirs_to_scan = [_user_dir(uid)]

    for d in dirs_to_scan:
        if not d.exists():
            continue
        for fp in d.glob("*.json"):
            try:
                conv = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            # 根目录的老格式文件只有 admin 能看到（dirs_to_scan 已控制范围）
            out.append(_summary(conv))

    out.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return jsonify({"items": out})


@bp.route("/conversations", methods=["POST"])
def create_conversation():
    uid = _current_user_id()
    if uid is None:
        return jsonify({"error": "未登录"}), 401

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip() or _DEFAULT_TITLE
    now = _now()
    conv = {
        "id": new_id(),
        "user_id": uid,
        "title": title[:_TITLE_MAX],
        "created_at": now,
        "updated_at": now,
        "summary": "",
        "compact_at": 0,
        "messages": [],
    }
    save_conversation(conv)
    return jsonify(_summary(conv))


@bp.route("/conversations/<cid>", methods=["GET"])
def get_conversation(cid: str):
    uid = _current_user_id()
    if uid is None:
        return jsonify({"error": "未登录"}), 401

    conv = find_conversation(cid) if _is_admin() else load_conversation(cid, uid)
    if conv is None:
        return jsonify({"error": "会话不存在"}), 404
    if not _check_ownership(conv):
        return jsonify({"error": "无权访问该会话"}), 403

    # 附加压缩次数（从 DB 读取，出错时默认 0，不影响主流程）
    owner = conv.get("user_id") or uid
    payload = dict(conv)
    payload["auto_compact_count"] = conv_stats.get_compact_count(owner, cid)
    return jsonify(payload)


@bp.route("/conversations/<cid>", methods=["PATCH"])
def rename_conversation(cid: str):
    uid = _current_user_id()
    if uid is None:
        return jsonify({"error": "未登录"}), 401

    conv = find_conversation(cid) if _is_admin() else load_conversation(cid, uid)
    if conv is None:
        return jsonify({"error": "会话不存在"}), 404
    if not _check_ownership(conv):
        return jsonify({"error": "无权修改该会话"}), 403

    data = request.get_json(silent=True) or {}
    new_title = (data.get("title") or "").strip()
    if not new_title:
        return jsonify({"error": "title 不能为空"}), 400
    conv["title"] = new_title[:_TITLE_MAX]
    conv["updated_at"] = _now()
    save_conversation(conv)
    return jsonify(_summary(conv))


@bp.route("/conversations/<cid>", methods=["DELETE"])
def delete_conversation(cid: str):
    uid = _current_user_id()
    if uid is None:
        return jsonify({"error": "未登录"}), 401

    safe = _safe_id(cid)
    if not safe:
        return jsonify({"error": "非法 id"}), 400

    conv = find_conversation(safe) if _is_admin() else load_conversation(safe, uid)
    if conv is None:
        return jsonify({"ok": True})  # 已不存在，幂等
    if not _check_ownership(conv):
        return jsonify({"error": "无权删除该会话"}), 403

    # 定位并删除文件
    owner_id = conv.get("user_id")
    if owner_id is not None:
        fp = _path(safe, int(owner_id))
    else:
        fp = CONVERSATIONS_DIR / f"{safe}.json"  # 老格式
    if fp.is_file():
        fp.unlink()
    return jsonify({"ok": True})


@bp.route("/conversations/<cid>/compact", methods=["POST"])
def compact_endpoint(cid: str):
    """手动触发一级压缩；keep_tail_pairs 可在 body 中覆盖。"""
    from . import services  # 延迟导入，避免循环依赖

    uid = _current_user_id()
    if uid is None:
        return jsonify({"error": "未登录"}), 401

    conv = find_conversation(cid) if _is_admin() else load_conversation(cid, uid)
    if conv is None:
        return jsonify({"error": "会话不存在"}), 404
    if not _check_ownership(conv):
        return jsonify({"error": "无权操作该会话"}), 403

    owner_id = int(conv.get("user_id") or uid)
    data = request.get_json(silent=True) or {}
    keep = int(data.get("keep_tail_pairs", LEVEL1_KEEP_TAIL_PAIRS))
    cleaner_cfg = services.load_cleaner_settings()
    result = compact_conversation(cid, owner_id, cleaner_cfg, keep_tail_pairs=keep)
    if "error" in result:
        return jsonify({"error": result["error"]}), 400
    return jsonify(result)
