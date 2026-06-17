"""agent_service —— 销售 Agent 的业务核心模块。

子包：
    rag/    —— 文档加载、分块、嵌入、混合检索、Agent 编排
    graph/  —— LangGraph 编译的数据清洗 / 入库流水线
    mcp/    —— MCP 服务接入预留位
"""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
CONFIG_PATH = PACKAGE_ROOT / "config.yaml"
DOCS_DIR = PACKAGE_ROOT / "docs"
WIKI_DIR = PACKAGE_ROOT / "wiki"
CHROMA_DIR = PACKAGE_ROOT / "chroma_persist"
CONVERSATIONS_DIR = PACKAGE_ROOT / "conversations"
LARK_CONVERSATIONS_DIR = PACKAGE_ROOT / "lark_conversations"
LARK_TOKENS_DIR = PACKAGE_ROOT / "lark_tokens"
SKILLS_ROOT = PROJECT_ROOT / "skills"
DOWNLOADS_DIR = PACKAGE_ROOT / "downloads"  # 工具生成的可下载产物（docx 等）

# 政策 skill 更新流（管理员专用，与正常 RAG/检索隔离）
POLICY_STAGING_DIR = PACKAGE_ROOT / "policy_staging"        # 上传的政策材料暂存（不进 DOCS_DIR/向量库）
POLICY_DRAFTS_DIR = PACKAGE_ROOT / "policy_skill_drafts"    # agent 生成的待审核草稿
SKILL_BACKUPS_DIR = PACKAGE_ROOT / "skill_backups"          # 发布前对被覆盖的 skill 文件做备份

POLICY_SKILL_MAKER = PROJECT_ROOT / "policy_skill_maker" / "SKILL.md"  # 草稿生成所用方法论（开发态 meta-skill）

TOKEN_DIR = PROJECT_ROOT / "token"  # 官方 DeepSeek 分词器目录（tokenizer.json 等），用于精确 token 计数

__all__ = [
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "CONFIG_PATH",
    "DOCS_DIR",
    "WIKI_DIR",
    "CHROMA_DIR",
    "CONVERSATIONS_DIR",
    "LARK_CONVERSATIONS_DIR",
    "LARK_TOKENS_DIR",
    "SKILLS_ROOT",
    "DOWNLOADS_DIR",
    "POLICY_STAGING_DIR",
    "POLICY_DRAFTS_DIR",
    "SKILL_BACKUPS_DIR",
    "POLICY_SKILL_MAKER",
    "TOKEN_DIR",
]
