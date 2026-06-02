"""共享 SocketIO 实例，防止循环导入。

app_user.py / app_admin.py 调用 socketio.init_app(app)，
lark_agent.py 和 mcp_manager 回调 import 本模块即可。
"""
from flask_socketio import SocketIO

# threading 模式：无需 eventlet/gevent
socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")
