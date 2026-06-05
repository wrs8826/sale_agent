"""内置工具 MCP Server（stdio 模式）。

通过 lark_mcp.json 的 mcpServers 注册，由 MultiServerMCPClient 在启动时自动拉起本进程。
工具实现集中在 builtin_tools.py，本文件只负责把它们暴露为 MCP 协议。

新增工具：在 builtin_tools.py 加 @tool 函数 → 在本文件加对应 @mcp_server.tool() 转发。
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── 路径自举：以子进程方式启动时确保项目根在 sys.path ────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP
from agent_service.mcp.builtin_tools import get_current_time as _get_current_time

mcp_server = FastMCP("builtin-tools")


# ── MCP 工具注册（转发到 builtin_tools.py 的实现） ────────────────────────────

@mcp_server.tool()
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前日期和时间。

    Args:
        timezone: IANA 时区名称，例如 "Asia/Shanghai"（默认）、"UTC"、
                  "America/New_York"、"Europe/London"。

    Returns:
        格式为 "YYYY-MM-DD HH:MM:SS 时区缩写" 的字符串。
    """
    return _get_current_time.invoke({"timezone": timezone})


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp_server.run()
