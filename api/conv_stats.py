"""对话统计 — MySQL 持久化模块。

表结构（自动建表）：
    conversation_stats (
        user_id            INT         NOT NULL,
        conversation_id    VARCHAR(64) NOT NULL,
        auto_compact_count INT         NOT NULL DEFAULT 0,
        last_compacted_at  DATETIME,
        PRIMARY KEY (user_id, conversation_id)
    )

公开接口：
    ensure_table()                          → 建表（幂等，app 启动时调用）
    get_compact_count(user_id, conv_id)     → int
    increment_compact_count(user_id, conv_id) → int   返回更新后的计数
    reset_compact_count(user_id, conv_id)   → bool  清零（L4 熔断后，持久化进 DB）
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import pymysql
import pymysql.cursors

# ── DB 连接（与 auth.py 保持一致） ──────────────────────────────────────────
_DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
_DB_PORT = int(os.getenv("DB_PORT", "3306"))
_DB_USER = os.getenv("DB_USER", "root")
_DB_PASS = os.getenv("DB_PASS", "abc123")
_DB_NAME = os.getenv("DB_NAME", "sales_agent")


def _get_conn():
    return pymysql.connect(
        host=_DB_HOST,
        port=_DB_PORT,
        user=_DB_USER,
        password=_DB_PASS,
        database=_DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ── 建表 ─────────────────────────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_stats (
    user_id            INT         NOT NULL,
    conversation_id    VARCHAR(64) NOT NULL,
    auto_compact_count INT         NOT NULL DEFAULT 0,
    last_compacted_at  DATETIME,
    PRIMARY KEY (user_id, conversation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def ensure_table() -> None:
    """建表（幂等）。app 启动时调用一次即可。"""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.close()
        print("[conv_stats] conversation_stats 表已就绪")
    except Exception as exc:
        print(f"[conv_stats] 建表失败（功能降级，计数不持久化）: {exc}")


# ── 读写接口 ──────────────────────────────────────────────────────────────────

def get_compact_count(user_id: int, conversation_id: str) -> int:
    """查询指定会话的自动压缩次数，表不存在或查询失败时返回 0。"""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT auto_compact_count FROM conversation_stats"
                " WHERE user_id = %s AND conversation_id = %s",
                (user_id, conversation_id),
            )
            row = cur.fetchone()
        conn.close()
        return int(row["auto_compact_count"]) if row else 0
    except Exception as exc:
        print(f"[conv_stats] get_compact_count 失败: {exc}")
        return 0


def increment_compact_count(user_id: int, conversation_id: str) -> int:
    """将自动压缩次数 +1，并更新 last_compacted_at。

    使用 INSERT … ON DUPLICATE KEY UPDATE 保证幂等（行不存在时自动创建）。
    返回更新后的计数；出错时返回 -1。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_stats
                    (user_id, conversation_id, auto_compact_count, last_compacted_at)
                VALUES (%s, %s, 1, %s)
                ON DUPLICATE KEY UPDATE
                    auto_compact_count = auto_compact_count + 1,
                    last_compacted_at  = %s
                """,
                (user_id, conversation_id, now, now),
            )
            cur.execute(
                "SELECT auto_compact_count FROM conversation_stats"
                " WHERE user_id = %s AND conversation_id = %s",
                (user_id, conversation_id),
            )
            row = cur.fetchone()
        conn.close()
        return int(row["auto_compact_count"]) if row else -1
    except Exception as exc:
        print(f"[conv_stats] increment_compact_count 失败: {exc}")
        return -1


def reset_compact_count(user_id: int, conversation_id: str) -> bool:
    """将自动压缩次数清零（L4 熔断后调用）。

    持久化进 DB（行不存在则建零行），刷新/重启不会丢失"刚熔断过"的状态。
    成功返回 True，出错 False（不中断主流程）。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_stats
                    (user_id, conversation_id, auto_compact_count, last_compacted_at)
                VALUES (%s, %s, 0, %s)
                ON DUPLICATE KEY UPDATE
                    auto_compact_count = 0,
                    last_compacted_at  = %s
                """,
                (user_id, conversation_id, now, now),
            )
        conn.close()
        return True
    except Exception as exc:
        print(f"[conv_stats] reset_compact_count 失败: {exc}")
        return False
