"""登录 / 注册 / 登出蓝图，基于 Flask session + MySQL 用户表。

路由：
    POST /auth/register    { username, password, phone, department } → 201 / 400 / 409
    POST /auth/login       { username, password } → 200 / 401
    POST /auth/admin-login { username, password } → 200 / 401 / 403
    POST /auth/logout      → 200
    GET  /auth/me          → { username, role } / 401
"""
from __future__ import annotations

import os
import re
from functools import wraps

import pymysql
import pymysql.cursors
from flask import Blueprint, jsonify, request, session

bp = Blueprint("auth", __name__, url_prefix="/auth")

# ── DB 连接 ───────────────────────────────────────────────────────────────────
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


# ── 辅助：验证密码 ────────────────────────────────────────────────────────────
def _check_password(plain: str, hashed: str) -> bool:
    from werkzeug.security import check_password_hash
    return check_password_hash(hashed, plain)


# ── 辅助：登录保护装饰器 ──────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "未登录"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "未登录"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "权限不足"}), 403
        return f(*args, **kwargs)
    return decorated


# ── 内部：查库验密 ───────────────────────────────────────────────────────────
def _authenticate(username: str, password: str):
    """返回用户行 dict，验证失败返回 None。"""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash, role, is_banned FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
        conn.close()
    except Exception as e:
        raise RuntimeError(f"数据库错误: {e}")
    if not row or not _check_password(password, row["password_hash"]):
        return None
    return row


# ── 路由 ──────────────────────────────────────────────────────────────────────
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


@bp.route("/register", methods=["POST"])
def register():
    """用户注册：仅限普通用户，角色固定为 user。"""
    data = request.get_json(force=True) or {}
    username   = (data.get("username")   or "").strip()
    password   = (data.get("password")   or "")
    phone      = (data.get("phone")      or "").strip()
    department = (data.get("department") or "").strip()

    # ── 基础校验 ─────────────────────────────────────────────────────────────
    if not username:
        return jsonify({"error": "用户名不能为空"}), 400
    if len(username) < 2 or len(username) > 32:
        return jsonify({"error": "用户名长度须在 2~32 个字符之间"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "密码不能少于 6 位"}), 400
    if not phone or not _PHONE_RE.match(phone):
        return jsonify({"error": "请输入有效的 11 位手机号"}), 400
    if not department:
        return jsonify({"error": "部门不能为空"}), 400

    from werkzeug.security import generate_password_hash
    hashed = generate_password_hash(password)

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, phone, department)"
                " VALUES (%s, %s, 'user', %s, %s)",
                (username, hashed, phone, department),
            )
        conn.close()
    except pymysql.err.IntegrityError:
        return jsonify({"error": "用户名已存在，请换一个"}), 409
    except Exception as e:
        return jsonify({"error": f"数据库错误: {e}"}), 500

    return jsonify({"ok": True, "username": username}), 201


@bp.route("/login", methods=["POST"])
def login():
    """用户端登录：允许 user 和 admin 角色。"""
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    try:
        row = _authenticate(username, password)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    if not row:
        return jsonify({"error": "用户名或密码错误"}), 401
    if row.get("is_banned"):
        return jsonify({"error": "该账号已被封禁，请联系管理员"}), 403

    session.clear()
    session["user_id"] = row["id"]
    session["username"] = username
    session["role"] = row["role"]
    session.permanent = True

    return jsonify({"username": username, "role": row["role"]})


@bp.route("/admin-login", methods=["POST"])
def admin_login():
    """管理员端登录：仅允许 admin 角色，普通用户账号会被拒绝。"""
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    try:
        row = _authenticate(username, password)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    if not row:
        return jsonify({"error": "用户名或密码错误"}), 401
    if row.get("is_banned"):
        return jsonify({"error": "该账号已被封禁，请联系管理员"}), 403

    if row["role"] != "admin":
        return jsonify({"error": "该账号无管理员权限"}), 403

    session.clear()
    session["user_id"] = row["id"]
    session["username"] = username
    session["role"] = row["role"]
    session.permanent = True

    return jsonify({"username": username, "role": row["role"]})


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.route("/me", methods=["GET"])
def me():
    if not session.get("user_id"):
        return jsonify({"error": "未登录"}), 401
    return jsonify({
        "user_id": session["user_id"],
        "username": session["username"],
        "role": session["role"],
    })
