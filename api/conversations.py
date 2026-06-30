"""会话持久化蓝图 + 两级历史压缩（支持按用户隔离）。

存储后端：MySQL `conversations` 表（元数据列 + JSON body 列）。
    id / user_id / title / has_summary / compact_at / message_count / created_at / updated_at
    为便于列表查询与排序的去规范化元数据列；body 列存整个会话 JSON（消息体为文档形状，不拆表）。

每个会话 JSON（= body 列内容，亦为各业务函数读写的 conv dict）结构：
    {
        "id":         "<uuid4 hex>",
        "user_id":    <int>,           ← 归属用户（MySQL users.id）
        "title":      "首条用户消息截取的标题（可改名）",
        "created_at": "ISO-8601",
        "updated_at": "ISO-8601",
        "summary":    "中间折叠段的事实摘要（空表示从未压缩）",
        "compact_at": <int>,           ← 中间折叠段的末尾下标：messages[head_end:compact_at] 已折进 summary，
                                          messages[:head_end]（头部）与 messages[compact_at:]（尾部）逐字保留
        "messages":   [ {role, content, ts}, ... ]
    }

提供路由：
    GET    /conversations                       列表（普通用户只看自己的，admin 看全部）—— 仅查元数据列，不读 body
    POST   /conversations                       新建空会话（绑定当前 session user_id）
    GET    /conversations/<id>                  取完整会话（校验归属）
    PATCH  /conversations/<id>                  重命名（校验归属）
    DELETE /conversations/<id>                  删除（校验归属）
    POST   /conversations/<id>/compact          手动触发压缩（校验归属）

迁移与兼容：
    · 启动时 ensure_table() 建表并把历史 `agent_service/conversations/**/<uuid>.json`
      幂等导入库（migrate_files_to_db）；旧文件保留在磁盘作为只读备份，不删除。
    · 读路径（load/find_conversation）在库内 miss 时回退读旧文件（只读），覆盖尚未迁移的会话；
      写路径（save_conversation）只写库（库为唯一事实源）。
    · CONVERSATIONS_DIR 仍用于：会话级 filelock 锁文件 + 旧 JSON 只读备份。
    · 无 user_id 的根目录老格式文件无法判定归属，不入库、不进列表，但可经 find_conversation 直读。
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pymysql
import pymysql.cursors
from flask import Blueprint, current_app, jsonify, request, session

from agent_service import CONVERSATIONS_DIR
from agent_service.graph import build_cleaning_graph
from agent_service.logging_config import get_logger
from . import conv_stats

log = get_logger(__name__)

# 跨进程文件锁（app_user / app_admin 是两个进程，会同写同一会话文件）。
# 缺失时降级为「仅进程内 threading 锁」并告警——功能可用，但跨进程不互斥。
try:
    from filelock import FileLock, Timeout as _FileLockTimeout
    _HAS_FILELOCK = True
except Exception:  # pragma: no cover - 依赖缺失时的兜底
    _HAS_FILELOCK = False
    print("[conversations] 未安装 filelock，会话锁降级为仅进程内（跨进程不互斥）")

bp = Blueprint("conversations", __name__)


# ── 压缩相关常量 ─────────────────────────────────────────────────────────────
MAX_CONTEXT_TOKENS = 1_000_000   # token 兜底预算（模型上下文 1M）；活跃区 token 超 80% 时即便轮数不够也强制压缩
COMPACT_THRESHOLD = 0.80
TOOL_STORE_MAX = 4000   # 工具结果落盘截断上限（控存储/回放体积；Phase 0 工具轮持久化）
# 发送态裁剪（Phase 1/2，不动存储，UI 仍可见全部）
SEND_WINDOW_TURNS = 20        # L1：每次最多发最近 N 轮（以 user 消息为轮边界）；摘要恒前置——压缩已把活跃区压到 ~头+尾，此项仅兜底
TOOL_KEEP_RECENT_TURNS = 10   # L2：窗口内仅最近 M 轮保留工具消息，更早的工具消息剪掉
# 头尾保留 + 中间折叠（核心方案）：压缩后逐字保留「最初 HEAD 轮 + 最近 TAIL 轮」，中间段折进 summary。
# 头部 = 用户最初的任务/背景锚点，尾部 = 近期上下文，被丢弃的中间一定先进摘要（杜绝静默丢失）。
HEAD_KEEP_TURNS = 3
TAIL_KEEP_TURNS = 10
# 主触发：逐字（未折叠）轮数 > HEAD+TAIL+MARGIN 时自动压缩。留余量是为批量折叠，避免一过线就每轮调 LLM。
AUTO_COMPACT_MARGIN = 4
# L3 滚动摘要：手动 compact 与自动压缩统一为 L3，保留尾部按"轮"；头部由 compact 算法结构性保留。
L3_KEEP_TAIL_TURNS = TAIL_KEEP_TURNS
# L4 熔断：自动压缩累计达 CIRCUIT_BREAK_AFTER 次时清零计数并发 circuit_break 事件（"压缩频繁"提示）。
# 头尾方案下尾部恒保留，故 L4 不再清空尾部（=L3 的保留口径），仅承担提示 + 计数复位职责。
CIRCUIT_BREAK_AFTER = 3
L4_KEEP_TAIL_TURNS = TAIL_KEEP_TURNS

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
    """返回会话文件的绝对路径（旧格式只读备份 / 锁文件定位用）。"""
    return _user_dir(user_id) / f"{cid}.json"


# ── MySQL 存储后端 ──────────────────────────────────────────────────────────
# 连接参数与 auth.py / conv_stats.py 保持一致（优先环境变量，兜底硬编码）。
_DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
_DB_PORT = int(os.getenv("DB_PORT", "3306"))
_DB_USER = os.getenv("DB_USER", "root")
_DB_PASS = os.getenv("DB_PASS", "abc123")
_DB_NAME = os.getenv("DB_NAME", "sales_agent")


def _get_conn():
    return pymysql.connect(
        host=_DB_HOST, port=_DB_PORT, user=_DB_USER, password=_DB_PASS,
        database=_DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    )


# created_at / updated_at 存为 ISO-8601 字符串（与 _now() 一致）：固定格式下
# 字典序 == 时间序，故 ORDER BY updated_at 即按时间排序，与旧文件实现完全一致，
# 且免去时区/格式转换的坑。body 存整个会话 JSON。
_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id            VARCHAR(64)  NOT NULL PRIMARY KEY,
    user_id       INT          NOT NULL,
    title         VARCHAR(255) NOT NULL DEFAULT '',
    has_summary   TINYINT(1)   NOT NULL DEFAULT 0,
    compact_at    INT          NOT NULL DEFAULT 0,
    message_count INT          NOT NULL DEFAULT 0,
    created_at    VARCHAR(40)  NOT NULL DEFAULT '',
    updated_at    VARCHAR(40)  NOT NULL DEFAULT '',
    body          LONGTEXT     NOT NULL,
    KEY idx_user_updated (user_id, updated_at),
    KEY idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def ensure_table() -> None:
    """建表（幂等）+ 首次启动把历史 JSON 文件导入库。app 启动时调用一次。"""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.close()
        log.info("conversations 表已就绪")
    except Exception as exc:
        log.error("conversations 建表失败（会话功能将不可用）: %s", exc)
        return
    try:
        migrated = migrate_files_to_db()
        if migrated:
            log.info("已迁移 %d 个历史会话文件入库", migrated)
    except Exception as exc:
        log.error("历史会话文件迁移失败（不影响启动）: %s", exc)


def _db_upsert(conv: Dict) -> None:
    """把整个 conv dict 写入库（INSERT … ON DUPLICATE KEY UPDATE）。
    created_at / user_id 仅在首次插入时写入，更新时保持不变。"""
    msgs = conv.get("messages") or []
    body = json.dumps(conv, ensure_ascii=False)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversations
                    (id, user_id, title, has_summary, compact_at, message_count,
                     created_at, updated_at, body)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    title=VALUES(title), has_summary=VALUES(has_summary),
                    compact_at=VALUES(compact_at), message_count=VALUES(message_count),
                    updated_at=VALUES(updated_at), body=VALUES(body)
                """,
                (
                    str(conv["id"]), int(conv["user_id"]),
                    (conv.get("title") or _DEFAULT_TITLE)[:255],
                    1 if (conv.get("summary") or "").strip() else 0,
                    int(conv.get("compact_at", 0) or 0),
                    len(msgs),
                    conv.get("created_at") or "",
                    conv.get("updated_at") or "",
                    body,
                ),
            )
    finally:
        conn.close()


def _db_delete(safe_cid: str, user_id: int) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM conversations WHERE id=%s AND user_id=%s",
                (safe_cid, int(user_id)),
            )
    finally:
        conn.close()


def _list_summaries(user_id: int, is_admin: bool) -> List[Dict]:
    """仅查元数据列拼出列表（不读 body），返回与 _summary() 同形的 dict 列表。"""
    sql = (
        "SELECT id, user_id, title, has_summary, compact_at, message_count, "
        "created_at, updated_at FROM conversations "
    )
    params: tuple = ()
    if not is_admin:
        sql += "WHERE user_id=%s "
        params = (int(user_id),)
    sql += "ORDER BY updated_at DESC"
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{
        "id": r["id"],
        "user_id": r["user_id"],
        "title": r["title"] or _DEFAULT_TITLE,
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "message_count": int(r["message_count"] or 0),
        "has_summary": bool(r["has_summary"]),
        "compact_at": int(r["compact_at"] or 0),
    } for r in rows]


# ── 旧文件只读回退 + 一次性迁移 ─────────────────────────────────────────────
def _file_load(safe_cid: str, user_id: int) -> Optional[Dict]:
    fp = _path(safe_cid, user_id)
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _file_find(safe_cid: str) -> Optional[Dict]:
    if not CONVERSATIONS_DIR.exists():
        return None
    for user_dir in CONVERSATIONS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        fp = user_dir / f"{safe_cid}.json"
        if fp.is_file():
            try:
                return json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
    fp = CONVERSATIONS_DIR / f"{safe_cid}.json"   # 根目录老格式
    if fp.is_file():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _file_remove(safe_cid: str, user_id: int) -> None:
    """删除旧文件备份（防只读回退把已删会话"复活"）；失败静默。"""
    try:
        fp = _path(safe_cid, user_id)
        if fp.is_file():
            fp.unlink()
    except Exception:
        pass


def migrate_files_to_db() -> int:
    """把 CONVERSATIONS_DIR 下所有历史 *.json 幂等导入库（已在库的 id 跳过）。

    文件保留在磁盘作为只读备份，不删除。无 user_id 的根目录老格式文件用目录名兜底，
    无法判定归属则跳过（仍可经 find_conversation 直读，但不入库、不进列表）。返回新导入条数。
    """
    if not CONVERSATIONS_DIR.exists():
        return 0
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM conversations")
            existing = {r["id"] for r in cur.fetchall()}
        conn.close()
    except Exception as exc:
        log.error("迁移：读取库内现有会话 id 失败: %s", exc)
        return 0

    count = 0
    dirs = [CONVERSATIONS_DIR] + [d for d in CONVERSATIONS_DIR.iterdir() if d.is_dir()]
    for d in dirs:
        for fp in d.glob("*.json"):
            # 文件恒命名为 <id>.json，故先按文件名跳过已迁移项，免去重复读盘
            if fp.stem in existing:
                continue
            try:
                conv = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            cid = conv.get("id") or fp.stem
            if cid in existing:
                continue
            if conv.get("user_id") is None:
                # 子目录名即 user_id；根目录无 user_id 的老文件无法判定归属 → 跳过
                if d is not CONVERSATIONS_DIR and d.name.isdigit():
                    conv["user_id"] = int(d.name)
                else:
                    continue
            conv["id"] = cid
            try:
                _db_upsert(conv)
                existing.add(cid)
                count += 1
            except Exception as exc:
                log.error("迁移会话 %s 失败: %s", cid, exc)
    return count


# ── 会话级互斥锁（单飞 + 防并发丢更新）──────────────────────────────────────
# 同一会话同一时刻只允许一个「生成/压缩/改名/删除」在跑：
#   · /agent/chat 入口取锁、贯穿整段流式持有，第二个并发请求取不到 → 409；
#     用户须先「中断」当前回答（前端断开 SSE → 服务端 finally 放锁）才能再发。
#   · 进程内用 threading.Lock，进程间用 filelock（app_user/app_admin 互斥）。
# 锁只加在 4 个入口（chat / compact / rename / delete），核心读写函数本身不取锁，
# 故无嵌套、无重入需求；非阻塞或短等待取不到即抛 ConversationBusy。
CHAT_LOCK_WAIT = 10.0     # /agent/chat 取锁最长等待秒数（覆盖「中断后立刻重发」的释放窗口）
MUTATE_LOCK_WAIT = 8.0    # 压缩 / 改名 / 删除取锁最长等待秒数

_tlocks: Dict[str, threading.Lock] = {}
_flocks: Dict[str, "FileLock"] = {}
_locks_guard = threading.Lock()


class ConversationBusy(Exception):
    """会话已有进行中的生成/压缩，未能在等待时限内取得锁（调用方应回 409）。"""


def _get_tlock(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _tlocks.get(key)
        if lk is None:
            lk = threading.Lock()
            _tlocks[key] = lk
        return lk


def _get_flock(safe_cid: str, user_id: int):
    if not _HAS_FILELOCK:
        return None
    d = _user_dir(int(user_id))
    d.mkdir(parents=True, exist_ok=True)
    path = str(d / f"{safe_cid}.json.lock")
    with _locks_guard:
        fl = _flocks.get(path)
        if fl is None:
            fl = FileLock(path)
            _flocks[path] = fl
        return fl


class _ConvGate:
    """已取得的会话锁句柄；持有方负责调用一次 .release()（幂等）。"""

    def __init__(self, tlock: threading.Lock, flock):
        self._tlock = tlock
        self._flock = flock
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._flock is not None:
            try:
                if self._flock.is_locked:
                    self._flock.release()
            except Exception:
                pass
        try:
            self._tlock.release()
        except Exception:
            pass


def acquire_conversation(cid: str, user_id: Optional[int], wait: float = 0.0) -> _ConvGate:
    """取得会话级互斥锁；wait 秒内取不到 → 抛 ConversationBusy。调用方负责 release()。

    供 /agent/chat 这类「视图取锁、生成器里释放」的跨边界场景使用。
    """
    safe = _safe_id(cid)
    if not safe or user_id is None:
        raise ConversationBusy("无效会话")
    key = f"{int(user_id)}/{safe}"
    tlock = _get_tlock(key)
    got = tlock.acquire(timeout=wait) if wait and wait > 0 else tlock.acquire(blocking=False)
    if not got:
        raise ConversationBusy("会话正忙")
    flock = _get_flock(safe, int(user_id))
    if flock is not None:
        try:
            flock.acquire(timeout=wait if wait and wait > 0 else 0)
        except _FileLockTimeout:
            tlock.release()
            raise ConversationBusy("会话正忙（其它进程占用）")
        except Exception:
            # filelock 自身异常不应阻断主流程：降级为仅进程内锁
            flock = None
    return _ConvGate(tlock, flock)


@contextmanager
def conversation_lock(cid: str, user_id: Optional[int], wait: float = 0.0):
    """会话锁上下文管理器：用于单函数内自包含的读改写（压缩/改名/删除）。"""
    gate = acquire_conversation(cid, user_id, wait=wait)
    try:
        yield
    finally:
        gate.release()


# ── CRUD 基础函数 ─────────────────────────────────────────────────────────────

def load_conversation(cid: str, user_id: int) -> Optional[Dict]:
    """加载指定用户的会话（库优先，库内 miss 回退读旧文件）。找不到返回 None。"""
    safe = _safe_id(cid)
    if not safe:
        return None
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT body FROM conversations WHERE id=%s AND user_id=%s",
                (safe, int(user_id)),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return json.loads(row["body"])
    except Exception as exc:
        log.error("load_conversation 查库失败 (%s): %s", safe, exc)
    return _file_load(safe, user_id)   # 未迁移的旧文件只读回退


def find_conversation(cid: str) -> Optional[Dict]:
    """跨所有用户搜索会话（仅供 admin 或内部使用）：库优先，miss 回退旧文件。"""
    safe = _safe_id(cid)
    if not safe:
        return None
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT body FROM conversations WHERE id=%s", (safe,))
            row = cur.fetchone()
        conn.close()
        if row:
            return json.loads(row["body"])
    except Exception as exc:
        log.error("find_conversation 查库失败 (%s): %s", safe, exc)
    return _file_find(safe)


def save_conversation(conv: Dict) -> None:
    """写入库（INSERT … ON DUPLICATE KEY UPDATE，库为唯一事实源）。"""
    user_id = conv.get("user_id")
    if user_id is None:
        raise ValueError("save_conversation: conv 缺少 user_id 字段")
    _db_upsert(conv)


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

def append_turn(
    cid: str,
    user_id: int,
    user_text: str,
    assistant_text: str,
    tool_items: Optional[List[Dict]] = None,
) -> Optional[Dict]:
    """追加一轮对话（user +（可选）工具消息 + assistant）。返回更新后的 conv 或 None。

    tool_items: [{"name", "args", "result"}, ...]，来自 call_tools_node。
                工具结果按 TOOL_STORE_MAX 截断后，以 role=tool 消息持久化进历史（Phase 0）。
    """
    conv = load_conversation(cid, user_id)
    if conv is None:
        return None
    ts = _now()
    conv["messages"].append({"role": "user", "content": user_text, "ts": ts})
    for it in (tool_items or []):
        content = str(it.get("result") or "")
        if len(content) > TOOL_STORE_MAX:
            content = content[:TOOL_STORE_MAX] + f"\n…（工具结果过长，已截断，原 {len(content)} 字）"
        conv["messages"].append({
            "role": "tool",
            "name": it.get("name") or "",
            "args": it.get("args") or {},
            "content": content,
            "ts": ts,
        })
    conv["messages"].append({"role": "assistant", "content": assistant_text, "ts": ts})
    conv["updated_at"] = ts
    if conv.get("title") in (None, "", _DEFAULT_TITLE):
        conv["title"] = make_title_from_msg(user_text)
    save_conversation(conv)
    return conv


def _msg_view(m: Dict) -> Dict[str, str]:
    """把存储态消息投影成发送态视图（tool 保留 name，其余仅 role/content）。"""
    role = m.get("role", "user")
    if role == "tool":
        return {"role": "tool", "name": m.get("name", ""), "content": m.get("content", "")}
    return {"role": role, "content": m.get("content", "")}


def _turn_end_index(messages: List[Dict], keep_head_turns: int) -> int:
    """返回「最初 keep_head_turns 轮」之后的下标（轮 = 以 user 消息为边界）。

    - keep_head_turns <= 0      → 0（无头部）
    - 总轮数 <= keep_head_turns  → len(messages)（全部都算头部）
    - 否则 → 第 keep_head_turns+1 个 user 消息的下标（= 头部末尾，恰在第 4 轮 user 之前）
    """
    if keep_head_turns <= 0:
        return 0
    user_idx = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(user_idx) <= keep_head_turns:
        return len(messages)
    return user_idx[keep_head_turns]


def get_history(cid: str, user_id: int) -> List[Dict[str, str]]:
    """给 /agent/chat 用：返回 [{role, content}, ...]。

    压缩后按**时间顺序**拼成「最初 HEAD 轮原文 + [中间历史摘要] + 最近 TAIL 轮原文」：
    summary 只覆盖中间被折叠的部分，头尾原文逐字保留——任何被折叠的内容都已进摘要，
    不会静默丢失。未压缩（compact_at=0）时原样回放全部消息。
    """
    conv = load_conversation(cid, user_id)
    if conv is None:
        return []
    messages = conv.get("messages", [])
    summary = (conv.get("summary") or "").strip()
    compact_at = int(conv.get("compact_at", 0) or 0)

    # 从未压缩（或异常无摘要）→ 原样回放全部消息
    if compact_at <= 0 or not summary:
        history: List[Dict[str, str]] = []
        if summary:
            history.append({"role": "system", "content": f"[历史摘要]\n{summary}"})
        history.extend(_msg_view(m) for m in messages)
        return history

    # 已压缩 → 头部原文 + 中间摘要 + 尾部原文（compact_at 标记中间折叠段的末尾）
    head_end = min(_turn_end_index(messages, HEAD_KEEP_TURNS), compact_at)
    history = [_msg_view(m) for m in messages[:head_end]]
    history.append({"role": "system", "content": f"[中间历史摘要]\n{summary}"})
    history.extend(_msg_view(m) for m in messages[compact_at:])
    return history


def should_auto_compact(
    cid: str,
    user_id: int,
    keep_head_turns: int = HEAD_KEEP_TURNS,
    keep_tail_turns: int = TAIL_KEEP_TURNS,
    margin: int = AUTO_COMPACT_MARGIN,
) -> bool:
    """主触发判定：逐字（未折叠）轮数是否超过 头+尾+余量。

    逐字轮数 = 头部恒保留的 keep_head_turns 轮 + 当前 compact_at 之后的尾部轮数；
    未压缩时即全部轮数。超过阈值说明中间已积累出可折叠的一段，应触发压缩。
    """
    conv = load_conversation(cid, user_id)
    if conv is None:
        return False
    messages = conv.get("messages", [])
    compact_at = int(conv.get("compact_at", 0) or 0)
    user_idx = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if compact_at <= 0:
        verbatim = len(user_idx)
    else:
        tail_turns = sum(1 for i in user_idx if i >= compact_at)
        verbatim = min(keep_head_turns, len(user_idx)) + tail_turns
    return verbatim > keep_head_turns + keep_tail_turns + margin


def window_history(
    history: List[Dict],
    window_turns: int = SEND_WINDOW_TURNS,
    tool_keep_turns: int = TOOL_KEEP_RECENT_TURNS,
) -> List[Dict]:
    """发送态裁剪（Phase 1 L1 窗口 + Phase 2 L2 工具裁剪）。

    输入 `history` 为 `get_history()` 的输出（头部原文 + [中间历史摘要] + 尾部原文）。
    本函数**不改存储**，只决定"这一轮真正发给模型的消息"：
      L1：以 user 消息为轮边界，仅保留最近 `window_turns` 轮。压缩后活跃区仅 ~头+尾(≈13)
          轮 < `window_turns`，故此项通常不裁剪，仅在压缩失败/未跑时兜底。
      L2：在窗口内，距今超过 `tool_keep_turns` 轮的 `role=tool` 消息剪掉（仅留 user/assistant）。

    轮边界定义：每条 user 消息开启一轮，其后的 tool/assistant 消息属于该轮。
    注：中间摘要 `system` 消息位于头部之后（非下标 0），不是 user/tool，既不计入轮数也不会被
    L2 剪掉。常态下活跃区 < `window_turns`，L1 不裁剪、摘要原位保留；极端降级（压缩长期失败、
    活跃区涨过窗口）下 L1 可能裁掉头部连带摘要，此时末尾兜底把摘要前置回插，确保永不丢失。
    """
    if not history:
        return history

    # 记录所有 system 摘要消息（压缩态它在中部）：若 L1 窗口把头部连同摘要一起裁掉，
    # 末尾兜底回插，确保「中间历史摘要」在任何情况下都不丢（含 LLM 长期不可用、活跃区
    # 涨过窗口的降级场景）。
    summary_msgs = [m for m in history if m.get("role") == "system"]

    # 仅当历史首条就是 system（未压缩态偶发的前置摘要）时单独摘出不计轮。
    head: List[Dict] = []
    body = history
    if history[0].get("role") == "system":
        head = [history[0]]
        body = history[1:]

    # 轮起点 = 各 user 消息在 body 中的下标
    user_pos = [i for i, m in enumerate(body) if m.get("role") == "user"]

    # L1：只留最近 window_turns 轮
    if len(user_pos) > window_turns:
        start = user_pos[len(user_pos) - window_turns]
        body = body[start:]
        user_pos = [p - start for p in user_pos[len(user_pos) - window_turns:]]

    # L2：窗口内距今 > tool_keep_turns 轮的工具消息剪掉
    tool_cutoff = user_pos[len(user_pos) - tool_keep_turns] if len(user_pos) > tool_keep_turns else 0
    trimmed = [m for i, m in enumerate(body) if not (m.get("role") == "tool" and i < tool_cutoff)]

    result = head + trimmed
    # 兜底：被 L1 裁掉的摘要前置回插（常态无裁剪时摘要仍在 result 中，不会重复）
    for sm in summary_msgs:
        if sm not in result:
            result = [sm] + result
    return result


# ── token 估算（Phase 3：委托到官方 DeepSeek 分词器，不可用时内部降级粗估）──────
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    from agent_service.token_counter import count_tokens
    return count_tokens(text)


def estimate_history_tokens(history: List[Dict[str, str]]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in history)


# ── 压缩 ──────────────────────────────────────────────────────────────────────
def _format_for_compaction(prior_summary: str, messages: List[Dict]) -> str:
    parts: List[str] = []
    if prior_summary:
        parts.append(f"[历史摘要]\n{prior_summary}\n")
    for m in messages:
        role = m.get("role")
        if role == "user":
            parts.append(f"用户：{m.get('content','')}")
        elif role == "tool":
            parts.append(f"工具[{m.get('name','')}]：{m.get('content','')}")
        else:
            parts.append(f"助手：{m.get('content','')}")
    return "\n".join(parts)


def _tail_start_by_turns(messages: List[Dict], keep_tail_turns: int) -> int:
    """返回"保留最近 keep_tail_turns 轮"的起点下标（轮 = 以 user 消息为边界）。

    - keep_tail_turns <= 0  → len(messages)（活跃区全部可压；供 L4 熔断用）
    - 总轮数 <= keep_tail_turns → 0（没有超出保留窗口的内容）
    - 否则 → 倒数第 keep_tail_turns 个 user 消息的下标
    工具消息计入对应轮，不单独成轮，故按 user 边界切分对工具持久化天然兼容。
    """
    if keep_tail_turns <= 0:
        return len(messages)
    user_idx = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(user_idx) <= keep_tail_turns:
        return 0
    return user_idx[len(user_idx) - keep_tail_turns]


def compact_conversation(
    cid: str,
    user_id: int,
    cleaner_cfg: Dict[str, str],
    keep_tail_turns: int,
    keep_head_turns: int = HEAD_KEEP_TURNS,
) -> Dict:
    """对单个会话执行 L3 压缩：折叠**中间段**进 summary，结构性保留首尾原文。

    保留 = 最初 keep_head_turns 轮（头部锚点）+ 最近 keep_tail_turns 轮（近期上下文）；
    折叠 = 二者之间的中间轮。keep_tail_turns / keep_head_turns 均按**轮**（user 边界）计。
    头部由 `fold_from = max(compact_at, head_end)` 保证永不被折叠。

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

    head_end = _turn_end_index(messages, keep_head_turns)
    tail_start = _tail_start_by_turns(messages, keep_tail_turns)
    fold_from = max(compact_at, head_end)   # 头部永不折叠；已折叠部分从 compact_at 续上

    if tail_start <= fold_from:
        return {"unchanged": True, "reason": "对话过短，无需压缩"}

    to_compact = messages[fold_from:tail_start]
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
        "compacted_count": tail_start - fold_from,            # 本轮折进摘要的消息数
        "kept_count": head_end + (len(messages) - tail_start),  # 逐字保留的头+尾消息数
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
    return session.get("role") == "admin" and current_app.config.get("IS_ADMIN_APP", False)


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

    # 仅查元数据列 + 走 (user_id, updated_at) 索引排序，不读 body：admin 全表、普通用户限本人。
    try:
        items = _list_summaries(uid, _is_admin())
    except Exception as exc:
        log.error("list_conversations 失败: %s", exc)
        return jsonify({"error": "读取会话列表失败"}), 500
    return jsonify({"items": items})


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

    owner_id = int(conv.get("user_id") or uid)
    try:
        with conversation_lock(cid, owner_id, wait=MUTATE_LOCK_WAIT):
            fresh = load_conversation(cid, owner_id) or conv   # 锁内重读，防丢更新
            fresh["title"] = new_title[:_TITLE_MAX]
            fresh["updated_at"] = _now()
            save_conversation(fresh)
            conv = fresh
    except ConversationBusy:
        return jsonify({"error": "该对话正在生成中，请稍后重试"}), 409
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

    # 删库行 + 清理旧文件备份
    owner_id = conv.get("user_id")
    if owner_id is not None:
        try:
            # 取锁后再删，避免删到一半另有生成在写（写回会复活僵尸会话）
            with conversation_lock(safe, int(owner_id), wait=MUTATE_LOCK_WAIT):
                _db_delete(safe, int(owner_id))
                _file_remove(safe, int(owner_id))   # 防只读回退把已删会话复活
        except ConversationBusy:
            return jsonify({"error": "该对话正在生成中，请先中断或稍后再删除"}), 409
    else:
        fp = CONVERSATIONS_DIR / f"{safe}.json"  # 老格式（无 user_id，不在库，仅磁盘）
        if fp.is_file():
            fp.unlink()
    return jsonify({"ok": True})


@bp.route("/conversations/<cid>/compact", methods=["POST"])
def compact_endpoint(cid: str):
    """手动触发 L3 压缩；keep_tail_turns 可在 body 中覆盖（按轮）。"""
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
    keep = int(data.get("keep_tail_turns", L3_KEEP_TAIL_TURNS))
    cleaner_cfg = services.load_cleaner_settings()
    try:
        # 取锁压缩：避免与同会话的生成/另一次压缩并发（跨进程也互斥）
        with conversation_lock(cid, owner_id, wait=MUTATE_LOCK_WAIT):
            result = compact_conversation(cid, owner_id, cleaner_cfg, keep_tail_turns=keep)
    except ConversationBusy:
        return jsonify({"error": "该对话正在生成中，请先中断或稍后再压缩"}), 409
    if "error" in result:
        return jsonify({"error": result["error"]}), 400
    return jsonify(result)
