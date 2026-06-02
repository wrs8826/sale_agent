"""兼容入口（保留旧命令可用）。

推荐使用：
    python -m api.app_user    # 用户端 · 端口 5001
    python -m api.app_admin   # 管理员端 · 端口 5002
"""
from api.app_user import app  # noqa: F401

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
