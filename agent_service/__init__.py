"""agent_service —— 销售 Agent 的业务核心模块。

子包：
    rag/    —— 文档加载、分块、嵌入、混合检索、Agent 编排
    graph/  —— LangGraph 编译的数据清洗 / 入库流水线
    mcp/    —— MCP 服务接入预留位
"""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PACKAGE_ROOT / "config.yaml"
DOCS_DIR = PACKAGE_ROOT / "docs"
WIKI_DIR = PACKAGE_ROOT / "wiki"
CHROMA_DIR = PACKAGE_ROOT / "chroma_persist"
CONVERSATIONS_DIR = PACKAGE_ROOT / "conversations"
LARK_CONVERSATIONS_DIR = PACKAGE_ROOT / "lark_conversations"

__all__ = [
    "PACKAGE_ROOT",
    "CONFIG_PATH",
    "DOCS_DIR",
    "WIKI_DIR",
    "CHROMA_DIR",
    "CONVERSATIONS_DIR",
    "LARK_CONVERSATIONS_DIR",
]
