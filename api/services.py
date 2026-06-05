"""共享服务层：把 RAG 索引、Reranker、Agent 模块、API 设置封装为可热失效的单例。

所有蓝图都通过本模块取实例，避免每个请求重建。
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from agent_service import CONFIG_PATH, DOCS_DIR, PACKAGE_ROOT, WIKI_DIR
from agent_service.skill_loader import all_refs_dirs
from agent_service.rag import (
    DashScopeReranker,
    DocumentLoader,
    HybridRetriever,
    RAGConfig,
    build_simple_rag,
)
from agent_service.security import decrypt, encrypt, mask

# ── 数据源权重兜底默认 ────────────────────────────────────────────────────────
_DEFAULT_SOURCE_WEIGHTS = {"docs": 1.0, "wiki": 0.7, "skill": 1.0}

# ── 三段 API 配置兜底默认 ─────────────────────────────────────────────────────
_DEFAULT_QWEN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_CHAT = {"api_key": "", "base_url": _DEFAULT_QWEN_BASE, "model_name": "qwen3-max"}
_DEFAULT_CLEANER: Dict[str, str] = {"api_key": "", "base_url": "", "model_name": ""}
_DEFAULT_RERANKER = {"api_key": "", "base_url": "", "model_name": "gte-rerank-v2"}
_DEFAULT_EMBEDDING = {"api_key": "", "base_url": "", "model_name": "text-embedding-v4"}

# ── 单例缓存 ──────────────────────────────────────────────────────────────────
_rag: Optional[HybridRetriever] = None
_rag_build_key: Tuple = ()
_reranker: Optional[DashScopeReranker] = None
_reranker_key: Tuple = ()


# ── 配置读取 ──────────────────────────────────────────────────────────────────
def load_config() -> RAGConfig:
    """每次请求都重新读取 config.yaml，支持热更新。"""
    return RAGConfig.load(CONFIG_PATH)


def _read_raw_yaml() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as r:
        return yaml.safe_load(r) or {}


def _write_raw_yaml(data: Dict[str, Any]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as w:
        yaml.safe_dump(data, w, allow_unicode=True, sort_keys=False)


def current_source_weights() -> dict:
    """从最新 config 中读取 source_weights，缺失字段回退到默认。"""
    try:
        cfg = load_config()
    except Exception as exc:
        print(f"[services] 读取 source_weights 失败，使用默认: {exc}")
        return dict(_DEFAULT_SOURCE_WEIGHTS)
    weights = dict(_DEFAULT_SOURCE_WEIGHTS)
    if cfg.source_weights:
        weights.update(cfg.source_weights)
    return weights


# ── Wiki 目录 ─────────────────────────────────────────────────────────────────
def get_wiki_dir() -> Path:
    """返回当前配置的 wiki 目录（读 config.yaml storage.wiki_dir）。
    未配置或路径为空时回退到默认 WIKI_DIR。目录不存在则自动创建。
    """
    raw = _read_raw_yaml()
    path_str = ((raw.get("storage") or {}).get("wiki_dir") or "").strip()
    if path_str:
        p = Path(path_str)
        if not p.is_absolute():
            p = PACKAGE_ROOT / p
    else:
        p = WIKI_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── 三段 API 设置 ─────────────────────────────────────────────────────────────
def _merge(defaults: Dict[str, str], raw: Optional[Dict[str, Any]]) -> Dict[str, str]:
    out = dict(defaults)
    if raw:
        for k in ("api_key", "base_url", "model_name"):
            v = raw.get(k)
            if v is not None:
                out[k] = str(v)
    return out


def _legacy_api_key() -> str:
    """从旧顶级字段 / 环境变量取 key 作为兜底。"""
    try:
        cfg = load_config()
        if cfg.api_key:
            return decrypt(cfg.api_key)
    except Exception:
        pass
    return os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""


def load_chat_settings() -> Dict[str, str]:
    """返回解密后的 chat 段（api_key/base_url/model_name 三键齐全）。"""
    raw = _read_raw_yaml().get("chat") or {}
    merged = _merge(_DEFAULT_CHAT, raw)
    merged["api_key"] = decrypt(merged["api_key"]) or _legacy_api_key()
    return merged


def load_cleaner_settings() -> Dict[str, str]:
    """cleaner 缺省字段继承 chat。返回字段总是齐全。"""
    raw = _read_raw_yaml().get("cleaner") or {}
    raw_merged = _merge(_DEFAULT_CLEANER, raw)
    chat = load_chat_settings()
    key = decrypt(raw_merged["api_key"]) if raw_merged["api_key"] else ""
    return {
        "api_key": key or chat["api_key"],
        "base_url": raw_merged["base_url"] or chat["base_url"],
        "model_name": raw_merged["model_name"] or chat["model_name"],
    }


def load_reranker_settings() -> Dict[str, str]:
    """reranker 缺省字段继承 chat（model_name 例外，有自己的默认）。"""
    raw = _read_raw_yaml().get("reranker") or {}
    raw_merged = _merge(_DEFAULT_RERANKER, raw)
    chat = load_chat_settings()
    key = decrypt(raw_merged["api_key"]) if raw_merged["api_key"] else ""
    return {
        "api_key": key or chat["api_key"],
        "base_url": raw_merged["base_url"] or chat["base_url"],
        "model_name": raw_merged["model_name"] or _DEFAULT_RERANKER["model_name"],
    }


def load_embedding_settings() -> Dict[str, str]:
    """embedding 缺省字段继承 chat（model_name 例外，有自己的默认）。"""
    raw = _read_raw_yaml().get("embedding") or {}
    raw_merged = _merge(_DEFAULT_EMBEDDING, raw)
    chat = load_chat_settings()
    key = decrypt(raw_merged["api_key"]) if raw_merged["api_key"] else ""
    return {
        "api_key": key or chat["api_key"],
        "base_url": raw_merged["base_url"] or chat["base_url"],
        "model_name": raw_merged["model_name"] or _DEFAULT_EMBEDDING["model_name"],
    }


def cfg_with_embedding(cfg: RAGConfig) -> RAGConfig:
    """把 embedding 段叠加到 RAGConfig 的 api_key/api_base/embedder_name 上。

    EmbedderFactory 仍读取 RAGConfig 的旧字段，这里做一次性覆盖，
    让 ingest（写入嵌入）与 query（查询嵌入）共享同一组 embedding 设置。
    """
    s = load_embedding_settings()
    return dataclasses.replace(
        cfg,
        api_key=s["api_key"] or cfg.api_key,
        api_base=s["base_url"] or cfg.api_base,
        embedder_name=s["model_name"] or cfg.embedder_name,
    )


def get_settings_masked() -> Dict[str, Any]:
    """GET /settings 用：API key 打码后返回。"""
    raw = _read_raw_yaml()
    out: Dict[str, Any] = {}
    for section, defaults in (
        ("chat", _DEFAULT_CHAT),
        ("cleaner", _DEFAULT_CLEANER),
        ("reranker", _DEFAULT_RERANKER),
        ("embedding", _DEFAULT_EMBEDDING),
    ):
        merged = _merge(defaults, raw.get(section))
        plain = decrypt(merged["api_key"])
        out[section] = {
            "api_key_mask": mask(plain),
            "api_key_set": bool(plain),
            "base_url": merged["base_url"],
            "model_name": merged["model_name"],
        }
    # Storage 段
    out["storage"] = {
        "wiki_dir": ((raw.get("storage") or {}).get("wiki_dir") or ""),
    }
    return out


def save_settings(payload: Dict[str, Any]):
    """POST /settings 用。返回 (masked_dict, embedding_changed: bool)。

    payload 形如：{"chat": {...}, "cleaner": {...}, "reranker": {...}}
    其中 api_key:
        - 字段缺失 / 空字符串 → 保留原值
        - 非空字符串          → 视为新明文，加密后写回
    base_url / model_name 缺失 → 保留原值；显式空串 → 写回空串（让该段回退到继承）。
    """
    raw = _read_raw_yaml()
    # 记录写入前的 embedding 配置（用于变更检测）
    _old_emb = dict(raw.get("embedding") or {})
    for section in ("chat", "cleaner", "reranker", "embedding"):
        if section not in payload:
            continue
        incoming = payload[section] or {}
        existing = dict(raw.get(section) or {})

        # api_key：空 → 保留；非空 → 加密替换
        if "api_key" in incoming:
            new_key = (incoming.get("api_key") or "").strip()
            if new_key:
                existing["api_key"] = encrypt(new_key)
            elif "api_key" not in existing:
                existing["api_key"] = ""

        for field in ("base_url", "model_name"):
            if field in incoming:
                existing[field] = (incoming.get(field) or "").strip()

        raw[section] = existing

    # Storage 段（wiki_dir）
    if "storage" in payload:
        incoming_storage = payload["storage"] or {}
        existing_storage = dict(raw.get("storage") or {})
        if "wiki_dir" in incoming_storage:
            existing_storage["wiki_dir"] = (incoming_storage.get("wiki_dir") or "").strip()
        raw["storage"] = existing_storage
        # 新目录提前创建，失效 RAG 缓存
        get_wiki_dir()

    _write_raw_yaml(raw)

    # 检测 embedding 配置是否实质性变更（任一字段不同即为变更）
    _new_emb = dict(raw.get("embedding") or {})
    embedding_changed = any(
        _old_emb.get(f) != _new_emb.get(f)
        for f in ("api_key", "base_url", "model_name")
    )

    invalidate_rag()
    invalidate_reranker()
    return get_settings_masked(), embedding_changed


def load_rag_threshold() -> float:
    """返回知识库命中分数阈值，低于该值时回退到会话上下文回答。"""
    try:
        val = _read_raw_yaml().get("chat", {}).get("rag_score_threshold")
        return float(val) if val is not None else 0.3
    except (TypeError, ValueError):
        return 0.3


def get_api_key() -> Optional[str]:
    """向后兼容：返回 chat 段的明文 key（embedder / 旧调用方使用）。"""
    return load_chat_settings()["api_key"] or None


# ── RAG 索引 ──────────────────────────────────────────────────────────────────
def _source_snapshot() -> Tuple[frozenset, frozenset, frozenset, Path]:
    """返回 (docs 文件集, wiki 文件集, skill 文件集, wiki_dir 路径)，作为缓存失效判定依据。"""
    wiki_dir = get_wiki_dir()
    docs = frozenset(f.name for f in DOCS_DIR.iterdir() if f.is_file()) if DOCS_DIR.exists() else frozenset()
    wiki = frozenset(f.name for f in wiki_dir.iterdir() if f.is_file()) if wiki_dir.exists() else frozenset()
    skill_files: set = set()
    for refs_dir in all_refs_dirs():
        if refs_dir.exists():
            skill_files.update(f.name for f in refs_dir.iterdir() if f.is_file())
    return docs, wiki, frozenset(skill_files), wiki_dir


def get_rag(
    chunk_size: int,
    chunk_overlap: int,
    separators: Optional[list],
) -> Tuple[Optional[HybridRetriever], bool]:
    """返回 (rag, rebuilt)。docs/wiki/skill 任一变化、分块参数变化时自动重建。"""
    global _rag, _rag_build_key
    docs_snap, wiki_snap, skill_snap, wiki_dir = _source_snapshot()
    sep_key = tuple(separators) if separators else None
    key = (docs_snap, wiki_snap, skill_snap, str(wiki_dir), chunk_size, chunk_overlap, sep_key)

    if not docs_snap and not wiki_snap and not skill_snap:
        _rag, _rag_build_key = None, ()
        return None, False
    if _rag is not None and key == _rag_build_key:
        return _rag, False

    base_cfg = load_config()
    cfg = dataclasses.replace(
        base_cfg,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=sep_key,
    )
    cfg = cfg_with_embedding(cfg)

    all_docs: list = []
    all_metas: list = []

    # ── 常规来源：docs / wiki ──────────────────────────────────────────────
    for source_type, root in (("docs", DOCS_DIR), ("wiki", wiki_dir)):
        if not root.exists():
            continue
        try:
            docs, metas = DocumentLoader.load(root, config=cfg)
        except Exception as exc:
            print(f"[services] 加载 {source_type} 目录失败: {exc}")
            continue
        for m in metas:
            m["source_type"] = source_type
        all_docs.extend(docs)
        all_metas.extend(metas)

    # ── Skill 来源：skills/*/references/ ─────────────────────────────────
    for refs_dir in all_refs_dirs():
        if not refs_dir.exists():
            continue
        try:
            docs, metas = DocumentLoader.load(refs_dir, config=cfg)
        except Exception as exc:
            print(f"[services] 加载 skill refs {refs_dir} 失败: {exc}")
            continue
        for m in metas:
            m["source_type"] = "skill"
        all_docs.extend(docs)
        all_metas.extend(metas)

    if not all_docs:
        _rag, _rag_build_key = None, ()
        return None, False

    _rag = build_simple_rag(all_docs, all_metas, config=cfg)
    _rag_build_key = key
    return _rag, True


def get_current_rag() -> Optional[HybridRetriever]:
    """返回当前缓存的 RAG 实例（不触发重建）。"""
    return _rag


def invalidate_rag() -> None:
    """让 RAG 缓存失效，下次 get_rag() 会重建。"""
    global _rag, _rag_build_key
    _rag = None
    _rag_build_key = ()


def apply_source_weights(hits: list, use_reranker: bool) -> list:
    """把每个 hit 的分数乘以来源权重并按主分重排。"""
    weights = current_source_weights()
    for h in hits:
        st = (h.get("metadata") or {}).get("source_type", "docs")
        weight = weights.get(st, 1.0)
        h["source_type"] = st
        h["source_weight"] = weight
        h["hybrid_score_raw"] = h.get("hybrid_score", 0.0)
        h["hybrid_score"] = h["hybrid_score_raw"] * weight
        if use_reranker and "rerank_score" in h:
            h["rerank_score_raw"] = h["rerank_score"]
            h["rerank_score"] = h["rerank_score_raw"] * weight

    key_fn = (lambda x: x.get("rerank_score", 0.0)) if use_reranker else (lambda x: x.get("hybrid_score", 0.0))
    return sorted(hits, key=key_fn, reverse=True)


# ── Reranker ──────────────────────────────────────────────────────────────────
def invalidate_reranker() -> None:
    global _reranker, _reranker_key
    _reranker = None
    _reranker_key = ()


def get_reranker() -> Optional[DashScopeReranker]:
    """根据 reranker 段的 (api_key, model_name) 缓存实例，配置变更时自动重建。"""
    global _reranker, _reranker_key
    s = load_reranker_settings()
    if not s["api_key"]:
        return None
    key = (s["api_key"], s["model_name"])
    if _reranker is not None and key == _reranker_key:
        return _reranker
    _reranker = DashScopeReranker(model_name=s["model_name"], api_key=s["api_key"])
    _reranker_key = key
    return _reranker


