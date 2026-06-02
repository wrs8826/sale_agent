"""API 配置接口：chat / cleaner / reranker / embedding 四段。

GET  /settings        → 返回所有段，API key 仅返回掩码与是否已设置。
POST /settings        → 写回，空 api_key 表示保留原值，非空则加密替换。
                        cleaner / reranker / embedding 段中 base_url / model_name 为空时
                        表示继承 chat 段；reranker.model_name 默认 gte-rerank-v2、
                        embedding.model_name 默认 text-embedding-v4。
POST /settings/test   → 对四段配置逐一发起极短探测请求，返回 {ok, error, latency_ms}。
"""
from __future__ import annotations

import dataclasses
import time

from flask import Blueprint, jsonify, request
from openai import OpenAI as _OpenAI

from agent_service.rag import DashScopeReranker, EmbedderFactory

from . import services

bp = Blueprint("settings", __name__)


@bp.route("/settings", methods=["GET"])
def get_settings():
    return jsonify({"settings": services.get_settings_masked()})


@bp.route("/settings", methods=["POST"])
def update_settings():
    data = request.get_json(silent=True) or {}
    payload = {k: v for k, v in data.items() if k in ("chat", "cleaner", "reranker", "embedding", "storage")}
    if not payload:
        return jsonify({"error": "请求体应包含 chat / cleaner / reranker / embedding 中至少一段"}), 400
    try:
        masked, embedding_changed = services.save_settings(payload)
    except Exception as exc:
        return jsonify({"error": f"保存失败: {exc}"}), 500
    return jsonify({"ok": True, "settings": masked, "embedding_changed": embedding_changed})


# ── 连通测试 ────────────────────────────────────────────────────────────────
def _timed(fn):
    """运行 fn() 并返回 (ok, error_msg, latency_ms)。"""
    t0 = time.perf_counter()
    try:
        fn()
        return True, "", int((time.perf_counter() - t0) * 1000)
    except Exception as e:
        return False, str(e)[:240], int((time.perf_counter() - t0) * 1000)


def _test_chat_like(cfg: dict) -> None:
    if not cfg["api_key"]:
        raise RuntimeError("未配置 API Key")
    client = _OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None, timeout=30)
    client.chat.completions.create(
        model=cfg["model_name"],
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=5,
        temperature=0.0,
    )


def _test_embedding() -> None:
    cfg = services.load_embedding_settings()
    if not cfg["api_key"]:
        raise RuntimeError("未配置 API Key")
    rag_cfg = services.cfg_with_embedding(services.load_config())
    # api_provider 留空时 EmbedderFactory 会落到本地 sentence-transformers，这里强制走 OpenAI 兼容
    rag_cfg = dataclasses.replace(rag_cfg, api_provider=rag_cfg.api_provider or "openai")
    embedder = EmbedderFactory.create(rag_cfg)
    vec = embedder.embed_query("ping")
    if not vec:
        raise RuntimeError("embed_query 返回空向量")


def _test_reranker() -> None:
    cfg = services.load_reranker_settings()
    if not cfg["api_key"]:
        raise RuntimeError("未配置 API Key")
    r = DashScopeReranker(model_name=cfg["model_name"], api_key=cfg["api_key"])
    out = r.rerank("ping", [{"text": "pong"}], 1)
    if not out:
        raise RuntimeError("rerank 返回空结果")


@bp.route("/settings/test", methods=["POST"])
def test_settings():
    """对四段配置逐一发起 ping 级别请求，返回每段连通状态。"""
    results = {}
    ok, err, ms = _timed(lambda: _test_chat_like(services.load_chat_settings()))
    results["chat"] = {"ok": ok, "error": err, "latency_ms": ms}
    ok, err, ms = _timed(lambda: _test_chat_like(services.load_cleaner_settings()))
    results["cleaner"] = {"ok": ok, "error": err, "latency_ms": ms}
    ok, err, ms = _timed(_test_embedding)
    results["embedding"] = {"ok": ok, "error": err, "latency_ms": ms}
    ok, err, ms = _timed(_test_reranker)
    results["reranker"] = {"ok": ok, "error": err, "latency_ms": ms}
    return jsonify({"results": results})
