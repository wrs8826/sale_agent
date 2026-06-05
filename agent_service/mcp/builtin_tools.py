"""内置工具的 LangChain @tool 版本。

本模块是所有内置工具的**单一实现来源**：
  - builtin_mcp_server.py 从这里导入函数，再封装成 MCP 工具（飞书路径）
  - QA 图的 call_tools_node 直接使用 BUILTIN_TOOLS 列表（网页路径）

新增工具：在本文件加一个 @tool 函数，再把它加入 BUILTIN_TOOLS 即可。
"""
from __future__ import annotations

from langchain_core.tools import tool

# ── 工具定义 ──────────────────────────────────────────────────────────────────

@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前日期和时间。

    Args:
        timezone: IANA 时区名称，例如 "Asia/Shanghai"（默认）、"UTC"、
                  "America/New_York"、"Europe/London"。

    Returns:
        格式为 "YYYY-MM-DD HH:MM:SS 时区缩写" 的字符串。
    """
    from datetime import datetime

    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, KeyError):
            tz = ZoneInfo("Asia/Shanghai")
    except ImportError:
        try:
            import pytz
            try:
                tz = pytz.timezone(timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                tz = pytz.timezone("Asia/Shanghai")
        except ImportError:
            tz = None

    now = __import__("datetime").datetime.now(tz) if tz else __import__("datetime").datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


# ── 工具列表（QA 图 call_tools_node 使用） ────────────────────────────────────
BUILTIN_TOOLS = [get_current_time]
