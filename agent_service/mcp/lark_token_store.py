"""飞书用户 access token 持久化存储。

存储路径：agent_service/lark_tokens/<safe_open_id>.json
文件格式：
{
  "open_id":       "ou_xxx",
  "access_token":  "u-xxx",
  "refresh_token": "ur-xxx",
  "expires_at":    1234567890,   ← Unix 时间戳（UTC）
  "name":          "张三",        ← 可选，授权时顺便存用户名
  "avatar_url":    "https://..."  ← 可选
}

对外接口：
    save(open_id, data)
    load(open_id) -> dict | None
    get_valid_token(open_id, app_id, app_secret) -> str | None  ← 自动续签
    clear(open_id) -> bool
    is_authorized(open_id) -> bool
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, Optional

from agent_service import LARK_TOKENS_DIR

# access_token 有效期内留 60 秒余量触发续签
_EXPIRY_MARGIN = 60


def _ensure_dir() -> None:
    LARK_TOKENS_DIR.mkdir(parents=True, exist_ok=True)


def _safe(open_id: str) -> str:
    return re.sub(r"[^\w\-]", "_", open_id or "unknown")[:64]


def _file(open_id: str) -> Path:
    return LARK_TOKENS_DIR / f"{_safe(open_id)}.json"


# ── 公开 API ──────────────────────────────────────────────────────────────────

def save(open_id: str, data: Dict) -> None:
    """持久化 token 数据（原子写）。"""
    _ensure_dir()
    fp = _file(open_id)
    payload = dict(data)
    payload["open_id"] = open_id
    # 如果接口返回 expires_in（秒），转换成绝对时间戳
    if "expires_in" in payload and "expires_at" not in payload:
        payload["expires_at"] = int(time.time()) + int(payload["expires_in"])
    tmp = fp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)


def load(open_id: str) -> Optional[Dict]:
    """读取 token 数据，文件不存在或损坏时返回 None。"""
    fp = _file(open_id)
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_authorized(open_id: str) -> bool:
    """判断该用户是否已完成过 OAuth 授权（token 文件存在）。"""
    return _file(open_id).exists()


def get_valid_token(open_id: str, app_id: str, app_secret: str) -> Optional[str]:
    """返回可立即使用的 access_token。

    - 未授权 → None
    - token 仍在有效期内 → 直接返回
    - token 即将过期或已过期 → 尝试用 refresh_token 续签后返回新 token
    - 续签失败（refresh_token 也过期）→ 删除本地数据，返回 None（需重新授权）
    """
    data = load(open_id)
    if not data or not data.get("access_token"):
        return None

    expires_at = data.get("expires_at", 0)
    if expires_at > time.time() + _EXPIRY_MARGIN:
        return data["access_token"]

    # 尝试续签
    refresh_tok = data.get("refresh_token")
    if not refresh_tok:
        clear(open_id)
        return None

    try:
        from agent_service.mcp.lark_oauth import refresh_user_token
        new_data = refresh_user_token(refresh_tok, app_id, app_secret)
        if new_data.get("access_token"):
            save(open_id, new_data)
            print(f"[lark_token_store] {open_id} token 续签成功")
            return new_data["access_token"]
    except Exception as exc:
        print(f"[lark_token_store] {open_id} token 续签失败: {exc}")

    # 续签失败，token 已完全失效，清除让用户重新授权
    clear(open_id)
    return None


def clear(open_id: str) -> bool:
    """删除 token 文件（返回 True 表示文件存在并已删除）。"""
    fp = _file(open_id)
    if fp.exists():
        fp.unlink()
        return True
    return False
