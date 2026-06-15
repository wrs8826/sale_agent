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
def load_policy_file(skill_name: str, filename: str) -> str:
    """读取指定 skill 的 references 目录中的政策文档文件，返回文件全文。

    适用场景：当用户询问某个人才政策的具体细节（申报条件、资金政策、
    操作流程等），且已通过 SKILL.md 文档地图确定了目标文件时调用本工具。

    Args:
        skill_name: skill 目录名，例如 "甬江人才政策"、"太仓人才政策"、
                    "无锡人才政策"、"成都人才政策"。
        filename:   文件名（含 .md 扩展名），例如 "申报条件_制造业.md"、
                    "甬才通_变更立项与经费.md"。仅传文件名，不含路径前缀。

    Returns:
        文件全文字符串；若文件不存在则返回错误说明。
    """
    from agent_service import SKILLS_ROOT
    path = SKILLS_ROOT / skill_name / "references" / filename
    if not path.exists():
        available = []
        refs_dir = SKILLS_ROOT / skill_name / "references"
        if refs_dir.exists():
            available = [f.name for f in sorted(refs_dir.iterdir()) if f.suffix == ".md"]
        if available:
            return f"文件 {filename!r} 不存在。{skill_name}/references/ 中可用文件：{available}"
        return f"文件 {filename!r} 不存在，或 skill {skill_name!r} 的 references 目录为空。"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"读取文件失败：{exc}"


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


def _is_table_row(line: str) -> bool:
    """判断是否为 Markdown 表格行（去空白后以 | 开头且含至少一个 |）。"""
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    """判断是否为表格分隔行，如 |---|:--:|---| 。"""
    s = line.strip().strip("|")
    cells = [c.strip() for c in s.split("|")]
    return bool(cells) and all(c and set(c) <= set("-: ") for c in cells)


def _split_row(line: str) -> list:
    """切分一行表格为单元格列表（去掉首尾的 |）。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _add_md_table(doc, rows: list) -> None:
    """把 Markdown 表格行（已剔除分隔行）渲染为带边框的 Word 表格，首行加粗作表头。"""
    cells_per_row = [_split_row(r) for r in rows]
    ncols = max(len(r) for r in cells_per_row)
    table = doc.add_table(rows=0, cols=ncols)
    try:
        table.style = "Table Grid"  # 带边框
    except Exception:
        pass
    for i, cells in enumerate(cells_per_row):
        row_cells = table.add_row().cells
        for j in range(ncols):
            text = cells[j] if j < len(cells) else ""
            row_cells[j].text = text
            if i == 0:  # 表头加粗
                for para in row_cells[j].paragraphs:
                    for run in para.runs:
                        run.bold = True


def _render_body(doc, body: str) -> None:
    """渲染章节正文：识别 Markdown 表格块转为 Word 表格，其余按段落输出。"""
    lines = body.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _is_table_row(line):
            # 收集连续的表格行
            block = []
            while i < n and _is_table_row(lines[i]):
                block.append(lines[i])
                i += 1
            # 过滤分隔行；若仍有内容则建表，否则退化为普通段落
            data_rows = [r for r in block if not _is_table_separator(r)]
            if data_rows:
                _add_md_table(doc, data_rows)
            continue
        text = line.strip()
        if text:
            doc.add_paragraph(text)
        i += 1


@tool
def generate_word_document(title: str, sections: list, filename: str = "") -> str:
    """把结构化内容生成为 Word(.docx) 文档并返回下载链接。

    适用场景：用户要求生成/导出软件说明书、设计文档、软著登记说明书、
    专利申请文件等可下载的 Word 文件时调用。先把要写入的内容组织成
    标题 + 章节列表，再调用本工具落盘。

    Args:
        title: 文档大标题，例如 "XX管理系统 软件说明书"。
        sections: 章节列表，每个元素是 dict：
            {
              "heading": "章节标题，如 一、概述",
              "body": "正文，可含多个段落（用 \\n 分隔）",
              "level": 1        # 可选，标题层级 1~3，默认 1，用于子章节缩进
            }
            正文留空（只给 heading）时仅输出标题，适合做分节占位。
        filename: 可选，自定义文件名（不含扩展名）；缺省用 title 生成。

    Returns:
        含 Markdown 下载链接的字符串；失败时返回错误说明。
        请把该下载链接原样呈现给用户。
    """
    import re
    import uuid

    from agent_service import DOWNLOADS_DIR

    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        return "生成失败：服务器未安装 python-docx，请先 pip install python-docx。"

    if not title or not str(title).strip():
        return "生成失败：title 不能为空。"
    if not isinstance(sections, list) or not sections:
        return "生成失败：sections 必须是非空列表。"

    try:
        doc = Document()

        # 设置正文默认字体，确保中文正常显示（西文 + 东亚字体）
        normal = doc.styles["Normal"]
        normal.font.name = "宋体"
        normal.font.size = Pt(11)
        try:
            from docx.oxml.ns import qn
            normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        except Exception:
            pass

        doc.add_heading(str(title).strip(), level=0)

        for sec in sections:
            if not isinstance(sec, dict):
                continue
            heading = str(sec.get("heading") or "").strip()
            body = str(sec.get("body") or "").strip()
            try:
                level = int(sec.get("level", 1))
            except (TypeError, ValueError):
                level = 1
            level = min(max(level, 1), 4)

            if heading:
                doc.add_heading(heading, level=level)
            if body:
                _render_body(doc, body)

        # 文件名：清洗非法字符 + 短 uuid 防覆盖
        base = (filename or title).strip()
        base = re.sub(r'[\\/:*?"<>|]', "_", base)[:60] or "document"
        out_name = f"{base}_{uuid.uuid4().hex[:8]}.docx"

        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        doc.save(str(DOWNLOADS_DIR / out_name))
    except Exception as exc:
        return f"生成 Word 文档失败：{exc}"

    return (
        f"文档已生成。请把以下下载链接原样提供给用户："
        f"[{base}.docx](/download/{out_name})"
    )


# ── 工具列表（QA 图 call_tools_node 使用） ────────────────────────────────────
BUILTIN_TOOLS = [get_current_time, load_policy_file, generate_word_document]


def build_tool_table() -> str:
    """生成内置工具清单（Markdown 表格），供系统提示词常驻注入。

    取每个工具 docstring 的首行作为用途摘要；无工具时返回空串。
    """
    if not BUILTIN_TOOLS:
        return ""
    lines = ["| 工具 | 用途 |", "|---|---|"]
    for t in BUILTIN_TOOLS:
        desc = (t.description or "").strip()
        first = desc.splitlines()[0].strip() if desc else ""
        first = first.replace("|", "｜")  # 防止破坏表格
        lines.append(f"| {t.name} | {first} |")
    return "\n".join(lines)
