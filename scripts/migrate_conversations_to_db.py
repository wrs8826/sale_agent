"""一次性迁移脚本：把历史会话 JSON 文件导入 MySQL `conversations` 表。

通常无需手动运行——用户端 / 管理端启动时 `ensure_table()` 会自动幂等迁移。
仅在以下场景手动跑：先建表后补迁、排查迁移结果、或想在不启动 Flask 的情况下迁移。

用法：
    python -m scripts.migrate_conversations_to_db          # 建表 + 迁移
    python -m scripts.migrate_conversations_to_db --dry-run  # 只统计待迁移条数，不写库

幂等：已在库的会话 id 会跳过；旧 JSON 文件保留在磁盘作为只读备份，不删除。
DB 连接走环境变量 DB_HOST/DB_PORT/DB_USER/DB_PASS/DB_NAME（与 auth.py 一致）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent_service import CONVERSATIONS_DIR
from api import conversations as conv_store


def _count_file_convs() -> int:
    if not CONVERSATIONS_DIR.exists():
        return 0
    dirs = [CONVERSATIONS_DIR] + [d for d in CONVERSATIONS_DIR.iterdir() if d.is_dir()]
    n = 0
    for d in dirs:
        for fp in d.glob("*.json"):
            try:
                conv = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            uid = conv.get("user_id")
            if uid is None and not (d is not CONVERSATIONS_DIR and d.name.isdigit()):
                continue  # 无法判定归属的根目录老文件不计
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="迁移历史会话 JSON 文件到 MySQL conversations 表")
    ap.add_argument("--dry-run", action="store_true", help="只统计可迁移条数，不建表、不写库")
    args = ap.parse_args()

    if args.dry_run:
        print(f"扫描 {CONVERSATIONS_DIR}")
        print(f"可迁移（含归属判定）会话文件数：{_count_file_convs()}")
        print("（dry-run：未写库；已在库的 id 在实际迁移时会被跳过）")
        return

    print("建表（幂等）…")
    conn = conv_store._get_conn()
    with conn.cursor() as cur:
        cur.execute(conv_store._CREATE_SQL)
    conn.close()

    print("迁移历史会话文件入库…")
    migrated = conv_store.migrate_files_to_db()
    print(f"完成：本次新导入 {migrated} 个会话（旧文件已保留为只读备份）。")


if __name__ == "__main__":
    main()
