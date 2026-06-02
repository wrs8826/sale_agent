"""用户管理蓝图（仅管理员可用）。

路由：
    POST   /users                        → 创建新用户（管理员）
    GET    /users                        → 列出所有非 admin 用户
    PATCH  /users/<id>                   → 更新基本信息（密码/手机/部门/封禁状态）
    DELETE /users/<id>                   → 删除用户
    GET    /users/<id>/settings          → 读取用户专属模型配置（api_key 脱敏）
    POST   /users/<id>/settings          → 保存用户专属模型配置（api_key 加密）
    POST   /users/<id>/settings/test     → 连通测试（用表单值，不保存）
"""
from __future__ import annotations

import json
import time
from typing import Dict

from flask import Blueprint, jsonify, request

from agent_service.security import decrypt, encrypt, mask
from api.auth import admin_required, _get_conn

bp = Blueprint("users", __name__, url_prefix="/users")

# ── 四段默认值 ─────────────────────────────────────────────────────────────────
_SECTIONS = ("chat", "cleaner", "reranker", "embedding")
_DEFAULTS = {
    "chat":      {"api_key": "", "base_url": "", "model_name": ""},
    "cleaner":   {"api_key": "", "base_url": "", "model_name": ""},
    "reranker":  {"api_key": "", "base_url": "", "model_name": ""},
    "embedding": {"api_key": "", "base_url": "", "model_name": ""},
}


def _load_user_settings_raw(uid: int) -> Dict:
    """从 DB 读取 user_settings JSON，返回 dict（可能为空）。"""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT user_settings FROM users WHERE id=%s AND role!='admin'", (uid,))
        row = cur.fetchone()
    conn.close()
    if not row:
        return {}
    raw = row.get("user_settings") or "{}"
    try:
        return json.loads(raw) or {}
    except Exception:
        return {}


def _mask_settings(raw: Dict) -> Dict:
    """把 api_key 脱敏后返回给前端。"""
    out = {}
    for sec in _SECTIONS:
        sec_raw = raw.get(sec) or {}
        api_key_enc = sec_raw.get("api_key") or ""
        plain = decrypt(api_key_enc) if api_key_enc else ""
        out[sec] = {
            "api_key_mask": mask(plain) if plain else "",
            "api_key_set":  bool(plain),
            "base_url":     sec_raw.get("base_url") or "",
            "model_name":   sec_raw.get("model_name") or "",
        }
    return out


def _resolve_for_test(user_raw: Dict, section: str, form_cfg: Dict) -> Dict:
    """合并测试用配置：表单值 → 用户已保存值 → 系统设置（按优先级）。"""
    from api import services

    sys_loaders = {
        "chat":      services.load_chat_settings,
        "cleaner":   services.load_cleaner_settings,
        "reranker":  services.load_reranker_settings,
        "embedding": services.load_embedding_settings,
    }
    sys_cfg = sys_loaders[section]()

    saved_sec = user_raw.get(section) or {}
    saved_key = decrypt(saved_sec.get("api_key") or "") if saved_sec.get("api_key") else ""

    # 表单传来的 api_key 优先；留空则用已保存的；再留空则用系统
    api_key = form_cfg.get("api_key") or saved_key or sys_cfg["api_key"]
    base_url = form_cfg.get("base_url") or saved_sec.get("base_url") or sys_cfg["base_url"]
    model_name = form_cfg.get("model_name") or saved_sec.get("model_name") or sys_cfg["model_name"]
    return {"api_key": api_key, "base_url": base_url, "model_name": model_name}


def _timed(fn):
    t0 = time.perf_counter()
    try:
        fn()
        return True, "", int((time.perf_counter() - t0) * 1000)
    except Exception as e:
        return False, str(e)[:240], int((time.perf_counter() - t0) * 1000)


def _test_chat_like(cfg: Dict) -> None:
    from openai import OpenAI
    if not cfg["api_key"]:
        raise RuntimeError("未配置 API Key")
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None, timeout=30)
    client.chat.completions.create(
        model=cfg["model_name"],
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=5,
        temperature=0.0,
    )


def _test_embedding_with(cfg: Dict) -> None:
    import dataclasses
    from agent_service.rag import EmbedderFactory, RAGConfig
    from api import services
    if not cfg["api_key"]:
        raise RuntimeError("未配置 API Key")
    base_cfg = services.load_config()
    rag_cfg = dataclasses.replace(
        base_cfg,
        api_key=cfg["api_key"],
        api_base=cfg["base_url"] or base_cfg.api_base,
        embedder_name=cfg["model_name"] or base_cfg.embedder_name,
        api_provider=base_cfg.api_provider or "openai",
    )
    embedder = EmbedderFactory.create(rag_cfg)
    vec = embedder.embed_query("ping")
    if not vec:
        raise RuntimeError("embed_query 返回空向量")


def _test_reranker_with(cfg: Dict) -> None:
    from agent_service.rag import DashScopeReranker
    if not cfg["api_key"]:
        raise RuntimeError("未配置 API Key")
    r = DashScopeReranker(model_name=cfg["model_name"], api_key=cfg["api_key"])
    out = r.rerank("ping", [{"text": "pong"}], 1)
    if not out:
        raise RuntimeError("rerank 返回空结果")


# ── 路由 ──────────────────────────────────────────────────────────────────────

@bp.route("", methods=["POST"])
@admin_required
def create_user():
    from werkzeug.security import generate_password_hash
    data = request.get_json(force=True) or {}
    username   = (data.get("username")   or "").strip()
    password   = (data.get("password")   or "")
    phone      = (data.get("phone")      or "").strip()
    department = (data.get("department") or "").strip() or "未分配"

    if not username or len(username) < 2:
        return jsonify({"error": "用户名至少 2 个字符"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    if not phone:
        return jsonify({"error": "手机号不能为空"}), 400

    hashed = generate_password_hash(password)
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, phone, department)"
                " VALUES (%s, %s, 'user', %s, %s)",
                (username, hashed, phone, department),
            )
            uid = cur.lastrowid
            cur.execute(
                "SELECT id, username, role, phone, department, is_banned, created_at"
                " FROM users WHERE id=%s", (uid,)
            )
            row = cur.fetchone()
        conn.commit()
        conn.close()
    except Exception as e:
        if "Duplicate entry" in str(e):
            return jsonify({"error": "用户名已存在"}), 409
        return jsonify({"error": str(e)}), 500

    row["is_banned"] = bool(row["is_banned"])
    row["chat_model"] = ""
    row["has_custom_settings"] = False
    if row.get("created_at"):
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify({"user": row}), 201


@bp.route("", methods=["GET"])
@admin_required
def list_users():
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role, phone, department, user_settings,"
                " is_banned, created_at FROM users WHERE role != 'admin'"
                " ORDER BY created_at DESC"
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    for row in rows:
        if row.get("created_at"):
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M")
        row["is_banned"] = bool(row["is_banned"])
        raw_settings = row.pop("user_settings") or "{}"
        try:
            parsed = json.loads(raw_settings)
        except Exception:
            parsed = {}
        # 简要摘要：chat model 名供列表展示
        row["chat_model"] = (parsed.get("chat") or {}).get("model_name") or ""
        row["has_custom_settings"] = bool(parsed)
    return jsonify({"users": rows})


@bp.route("/<int:uid>", methods=["PATCH"])
@admin_required
def update_user(uid: int):
    data = request.get_json(force=True) or {}
    sets, params = [], []

    if "password" in data:
        pwd = data["password"]
        if not pwd or len(pwd) < 6:
            return jsonify({"error": "密码不能少于 6 位"}), 400
        from werkzeug.security import generate_password_hash
        sets.append("password_hash = %s")
        params.append(generate_password_hash(pwd))

    if "phone" in data:
        import re
        phone = (data["phone"] or "").strip()
        if phone and not re.match(r"^1[3-9]\d{9}$", phone):
            return jsonify({"error": "手机号格式不正确"}), 400
        sets.append("phone = %s")
        params.append(phone)

    if "department" in data:
        sets.append("department = %s")
        params.append((data["department"] or "").strip())

    if "is_banned" in data:
        sets.append("is_banned = %s")
        params.append(1 if data["is_banned"] else 0)

    if not sets:
        return jsonify({"error": "没有要更新的字段"}), 400

    params.append(uid)
    sql = f"UPDATE users SET {', '.join(sets)} WHERE id = %s AND role != 'admin'"
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            affected = cur.execute(sql, params)
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not affected:
        return jsonify({"error": "用户不存在或无权修改"}), 404
    return jsonify({"ok": True})


@bp.route("/<int:uid>", methods=["DELETE"])
@admin_required
def delete_user(uid: int):
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            affected = cur.execute(
                "DELETE FROM users WHERE id = %s AND role != 'admin'", (uid,)
            )
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not affected:
        return jsonify({"error": "用户不存在或无权删除"}), 404
    return jsonify({"ok": True})


@bp.route("/<int:uid>/settings", methods=["GET"])
@admin_required
def get_user_settings(uid: int):
    try:
        raw = _load_user_settings_raw(uid)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"settings": _mask_settings(raw)})


@bp.route("/<int:uid>/settings", methods=["POST"])
@admin_required
def save_user_settings(uid: int):
    data = request.get_json(force=True) or {}
    try:
        raw = _load_user_settings_raw(uid)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    for sec in _SECTIONS:
        form_sec = data.get(sec) or {}
        saved_sec = raw.get(sec) or {}
        new_api_key = form_sec.get("api_key") or ""
        if new_api_key:
            enc_key = encrypt(new_api_key)
        else:
            enc_key = saved_sec.get("api_key") or ""
        raw[sec] = {
            "api_key":    enc_key,
            "base_url":   form_sec.get("base_url")   if "base_url"   in form_sec else saved_sec.get("base_url", ""),
            "model_name": form_sec.get("model_name") if "model_name" in form_sec else saved_sec.get("model_name", ""),
        }

    new_json = json.dumps(raw, ensure_ascii=False)
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            affected = cur.execute(
                "UPDATE users SET user_settings=%s WHERE id=%s AND role!='admin'",
                (new_json, uid),
            )
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not affected:
        return jsonify({"error": "用户不存在或无权修改"}), 404
    return jsonify({"ok": True, "settings": _mask_settings(raw)})


@bp.route("/<int:uid>/settings/test", methods=["POST"])
@admin_required
def test_user_settings(uid: int):
    """用前端表单值（未保存）逐段测试连通性。"""
    data = request.get_json(force=True) or {}
    try:
        raw = _load_user_settings_raw(uid)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    results = {}

    chat_cfg = _resolve_for_test(raw, "chat", data.get("chat") or {})
    ok, err, ms = _timed(lambda: _test_chat_like(chat_cfg))
    results["chat"] = {"ok": ok, "error": err, "latency_ms": ms}

    cleaner_cfg = _resolve_for_test(raw, "cleaner", data.get("cleaner") or {})
    ok, err, ms = _timed(lambda: _test_chat_like(cleaner_cfg))
    results["cleaner"] = {"ok": ok, "error": err, "latency_ms": ms}

    embedding_cfg = _resolve_for_test(raw, "embedding", data.get("embedding") or {})
    ok, err, ms = _timed(lambda: _test_embedding_with(embedding_cfg))
    results["embedding"] = {"ok": ok, "error": err, "latency_ms": ms}

    reranker_cfg = _resolve_for_test(raw, "reranker", data.get("reranker") or {})
    ok, err, ms = _timed(lambda: _test_reranker_with(reranker_cfg))
    results["reranker"] = {"ok": ok, "error": err, "latency_ms": ms}

    return jsonify({"results": results})
