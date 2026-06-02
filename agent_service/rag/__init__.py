"""RAG 子包：文档加载、分块、嵌入、混合检索、Reranker。

Agent 编排（extract → retrieve → generate）已迁至 agent_service.graph.qa，
不再从本子包暴露 build_agent / stream_events。
"""
from .simple_rag import (
    DashScopeReranker,
    DocumentChunker,
    DocumentLoader,
    EmbedderFactory,
    HybridRetriever,
    RAGConfig,
    build_rag_from_path,
    build_simple_rag,
)

__all__ = [
    "DashScopeReranker",
    "DocumentChunker",
    "DocumentLoader",
    "EmbedderFactory",
    "HybridRetriever",
    "RAGConfig",
    "build_rag_from_path",
    "build_simple_rag",
]
