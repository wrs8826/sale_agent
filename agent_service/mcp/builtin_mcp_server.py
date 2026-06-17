"""内置工具 MCP Server（stdio 模式）。

通过 lark_mcp.json 的 mcpServers 注册，由 MultiServerMCPClient 在启动时自动拉起本进程。
工具实现集中在 builtin_tools.py，本文件只负责把它们暴露为 MCP 协议。

本 server 暴露 **3 个核心工具**（与 `BUILTIN_TOOLS` 一致）：
    get_current_time / load_policy_file / generate_word_document
这三个会被注入飞书 ReAct Agent；文档读取工具（read_document / list_documents）刻意
**不在此暴露**，保持只走网页端（WEB_TOOLS）。

新增工具：在 builtin_tools.py 加 @tool 函数 → 若也要给飞书用，再在本文件加 @mcp_server.tool() 转发。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

# ── 路径自举：以子进程方式启动时确保项目根在 sys.path ────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# lark_mcp.json 与本文件同目录
_LARK_CFG_PATH = Path(__file__).resolve().parent / "lark_mcp.json"


def _public_base_url() -> str:
    """读取对外可访问的基地址，用于把下载相对链接重写为绝对链接（仅飞书路径需要）。

    优先 lark_mcp.json 的 `public_base_url`；缺失时回退到 `oauth_redirect_uri` 的 origin。
    取不到时返回空串（此时保持相对链接）。
    """
    try:
        cfg = json.loads(_LARK_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    base = (cfg.get("public_base_url") or "").strip().rstrip("/")
    if base:
        return base
    redirect = (cfg.get("oauth_redirect_uri") or "").strip()
    if redirect:
        from urllib.parse import urlparse
        u = urlparse(redirect)
        if u.scheme and u.netloc:
            return f"{u.scheme}://{u.netloc}"
    return ""

from mcp.server.fastmcp import FastMCP
from agent_service.mcp.builtin_tools import (
    get_current_time as _get_current_time,
    load_policy_file as _load_policy_file,
    generate_word_document as _generate_word_document,
)

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


@mcp_server.tool()
def load_policy_file(skill_name: str, filename: str) -> str:
    """读取指定 skill 的 references 目录中的政策文档文件，返回文件全文。

    适用场景：用户询问某地人才政策的具体细节（申报条件、资金政策、操作流程等），
    且已通过 SKILL.md 文档地图确定了目标文件时调用。

    Args:
        skill_name: skill 目录名，例如 "甬江人才政策"、"太仓人才政策"。
        filename:   文件名（含 .md 扩展名），仅传文件名，不含路径前缀。

    Returns:
        文件全文字符串；若文件不存在则返回错误说明。
    """
    return _load_policy_file.invoke({"skill_name": skill_name, "filename": filename})


@mcp_server.tool()
def generate_word_document(title: str, sections: List[dict], filename: str = "") -> str:
    """把结构化内容生成为 Word(.docx) 文档并返回下载链接。

    适用场景：用户要求生成/导出软件说明书、设计文档、软著登记说明书、专利申请文件等
    可下载的 Word 文件时调用。

    Args:
        title:    文档大标题。
        sections: 章节列表，每项为 dict：{"heading": "标题", "body": "正文", "level": 1}。
        filename: 可选，自定义文件名（不含扩展名）；缺省用 title 生成。

    Returns:
        含 Markdown 下载链接的字符串；请把链接原样呈现给用户。
    """
    result = _generate_word_document.invoke(
        {"title": title, "sections": sections, "filename": filename}
    )
    # 飞书路径：把相对下载链接重写为带域名的绝对链接，便于飞书用户直接点击
    base = _public_base_url()
    if base and "(/download/" in result:
        result = result.replace("(/download/", f"({base}/download/")
    return result


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp_server.run()
