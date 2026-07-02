"""Redis Session 配置（用户端 / 管理端共用）。

通过 flask-session 将 session 数据存入 Redis，实现：
  - 服务端 session（不依赖 cookie 加密存数据，仅签名 session ID）
  - 滑动窗口空闲超时：每次请求自动刷新 Redis TTL
  - 浏览器关闭后短期内免登录：持久 cookie + Redis 持久化

使用方式（在 create_app 中，secret_key 设置之后调用）：
    from api.session_store import configure_session
    configure_session(app, key_prefix="user_sess:")

配置项（可通过环境变量覆盖）：
    REDIS_HOST            默认 127.0.0.1
    REDIS_PORT            默认 6379
    REDIS_PASSWORD        默认 123456
    REDIS_DB              默认 0
    SESSION_IDLE_MINUTES  默认 240（空闲超时分钟数，即 4 小时）
"""
from __future__ import annotations

import os
from datetime import timedelta

# ── Redis 连接参数 ──────────────────────────────────────────────────────────
REDIS_HOST     = os.getenv("REDIS_HOST",     "127.0.0.1")
REDIS_PORT     = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "123456")
REDIS_DB       = int(os.getenv("REDIS_DB",   "0"))

# 空闲超时（无请求则 session 过期，用于退出到登录界面）
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "240"))
SESSION_IDLE_TIMEOUT = timedelta(minutes=SESSION_IDLE_MINUTES)


def _make_redis():
    """创建 Redis 连接（不 decode_responses，flask-session 需要 bytes）。"""
    import redis
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB,
        decode_responses=False,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def configure_session(app, *, key_prefix: str = "sess:") -> bool:
    """将 Flask app 的 session 后端切换到 Redis。

    必须在 app.secret_key 设置之后、第一个请求之前调用。

    参数：
        key_prefix  Redis key 前缀，用于区分用户端与管理端。
                    推荐：用户端 "user_sess:"，管理端 "admin_sess:"

    返回 True 表示 Redis 连通正常；False 表示 Redis 不可用（回退到 cookie session）。
    """
    try:
        from flask_session import Session
    except ImportError:
        print("[session_store] flask-session 未安装，使用默认 cookie session。"
              "请运行：pip install flask-session redis")
        # 回退：保留原有 PERMANENT_SESSION_LIFETIME 设置
        app.config.setdefault("PERMANENT_SESSION_LIFETIME", SESSION_IDLE_TIMEOUT)
        return False

    # 连通性测试
    try:
        r = _make_redis()
        r.ping()
    except Exception as exc:
        print(f"[session_store] Redis 连接失败（{exc}），使用默认 cookie session。")
        app.config.setdefault("PERMANENT_SESSION_LIFETIME", SESSION_IDLE_TIMEOUT)
        return False

    app.config.update(
        # ── flask-session 核心配置 ────────────────────────────────────────────
        SESSION_TYPE="redis",
        SESSION_REDIS=r,
        SESSION_KEY_PREFIX=key_prefix,
        SESSION_USE_SIGNER=True,          # 用 secret_key 签名 session ID cookie
        SESSION_PERMANENT=True,           # session 持久化（重启浏览器仍有效）

        # ── 超时配置 ──────────────────────────────────────────────────────────
        PERMANENT_SESSION_LIFETIME=SESSION_IDLE_TIMEOUT,

        # 每次响应都刷新 cookie 过期时间 + Redis TTL（实现滑动窗口空闲超时）
        SESSION_REFRESH_EACH_REQUEST=True,

        # ── Cookie 安全配置 ───────────────────────────────────────────────────
        SESSION_COOKIE_HTTPONLY=True,     # JS 不可读 cookie
        SESSION_COOKIE_SAMESITE="Lax",   # 防 CSRF
    )

    Session(app)
    print(f"[session_store] Redis session 已启用："
          f"{REDIS_HOST}:{REDIS_PORT} prefix={key_prefix} "
          f"idle_timeout={SESSION_IDLE_MINUTES}min")
    return True
