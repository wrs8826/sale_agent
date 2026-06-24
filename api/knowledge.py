"""知识库管理蓝图：文件 CRUD、清洗入库、RAG 检索测试、向量库维护。"""
from __future__ import annotations

import json
from pathlib import Path

import chromadb
from chromadb.config import Settings as _ChromaSettings
from flask import Blueprint, Response, jsonify, request, send_file, session, stream_with_context

from agent_service import CONFIG_PATH, DOCS_DIR, DOWNLOADS_DIR, POLICY_STAGING_DIR
from agent_service.graph import build_cleaning_graph
from agent_service.mcp.builtin_tools import extract_text_from_file
from agent_service.rag import DocumentChunker

from . import services

bp = Blueprint("knowledge", __name__)

ALLOWED_EXT = {".txt", ".md", ".rst", ".html", ".pdf", ".docx"}
# 二进制文档：清洗入库时按文本提取，但不回写原文件（回写会损坏二进制原件）
_BINARY_DOC_EXT = {".pdf", ".docx"}

_CLEAN_SYSTEM = (
    "你是一个专业的知识库文档处理助手。请先判断输入文本的类型，再按对应规则处理。\n\n"
    
    "【类型判断规则】\n"
    "- 聊天记录：含有对话式语句、问候寒暄、即时通讯风格（如微信/飞书私聊），"
    "通常有明显的发言人标记、时间戳、碎片化短句、表情符号等特征\n"
    "- 政策/档案：正式公文、规章制度、技术文档、学术资料、合同协议等，"
    "通常格式规整、使用书面语，有条款编号、章节标题，无对话结构\n\n"
    
    "【处理规则】\n\n"
    "▶ 若判断为【聊天记录】，执行清洗：\n"
    "  保留：项目讨论、产品需求、时间安排、联系方式、报价信息、技术要求、"
    "合作意向、会议安排、关键决策、跟进事项、重要承诺等业务相关内容\n"
    "  删除：问候寒暄（你好/再见/辛苦了等）、天气闲聊、纯社交话题、"
    "与业务无关的个人话题、单纯表情/符号/语气词\n"
    "  输出：直接返回清洗后的文本，保留原有对话结构（如「甲方：…乙方：…」），"
    "不加说明；若整段均为闲聊则返回空字符串\n\n"
    
    "▶ 若判断为【政策/档案】，直接返回：\n"
    "  不做任何修改，原样返回输入文本\n\n"
    
    "只输出处理后的纯文本，不加任何类型标注、解释或前后缀。"
)


# ── 文件 CRUD ────────────────────────────────────────────────────────────────
@bp.route("/files", methods=["GET"])
def list_files():
    files = sorted(f.name for f in DOCS_DIR.iterdir() if f.is_file())
    return jsonify(files)


@bp.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "请选择文件"}), 400
    f = request.files["file"]
    name = Path(f.filename).name
    if not name:
        return jsonify({"error": "文件名为空"}), 400
    if Path(name).suffix.lower() not in ALLOWED_EXT:
        return jsonify({"error": f"仅支持: {', '.join(sorted(ALLOWED_EXT))}"}), 400

    # kind=policy：政策材料走隔离暂存目录，不进 DOCS_DIR / 向量库，
    # 由「政策 skill 更新」流（admin）解析并生成 skill 草稿。仅 admin 可上传政策材料。
    kind = (request.form.get("kind") or "normal").strip().lower()
    if kind == "policy":
        if session.get("role") != "admin":
            return jsonify({"error": "仅管理员可上传政策材料"}), 403
        POLICY_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        f.save(POLICY_STAGING_DIR / name)
        return jsonify({"ok": True, "filename": name, "kind": "policy"})

    f.save(DOCS_DIR / name)
    services.invalidate_rag()
    return jsonify({"ok": True, "filename": name, "kind": "normal"})


@bp.route("/files/<filename>", methods=["DELETE"])
def delete_file(filename):
    target = DOCS_DIR / Path(filename).name
    if not target.is_file():
        return jsonify({"error": "文件不存在"}), 404
    target.unlink()
    services.invalidate_rag()
    return jsonify({"ok": True})


# ── 工具产物下载 ──────────────────────────────────────────────────────────────
@bp.route("/download/<path:filename>", methods=["GET"])
def download(filename):
    """下载 generate_word_document 等工具生成在 DOWNLOADS_DIR 的产物。"""
    name = Path(filename).name  # 仅取文件名，防目录穿越
    if not name or name != filename:
        return jsonify({"error": "非法文件名"}), 400
    target = DOWNLOADS_DIR / name
    if not target.is_file():
        return jsonify({"error": "文件不存在或已过期"}), 404
    return send_file(str(target), as_attachment=True, download_name=name)


# ── RAG 检索测试 ──────────────────────────────────────────────────────────────
@bp.route("/query", methods=["POST"])
def query():
    data = request.get_json(silent=True) or {}
    q = data.get("query", "").strip()
    if not q:
        return jsonify({"error": "查询内容不能为空"}), 400

    # 兜底使用 config.yaml（RAGConfig）；请求体显式提供则覆盖。
    # 用「key 在且非 None」判断，确保 bm25_weight=0.0（纯向量）这类合法假值不被吞掉。
    cfg = services.load_config()

    def _override(key, default, cast):
        return cast(data[key]) if data.get(key) is not None else default

    chunk_size = _override("chunk_size", cfg.chunk_size, int)
    chunk_overlap = _override("chunk_overlap", cfg.chunk_overlap, int)
    separators = data.get("separators") or (list(cfg.separators) if cfg.separators else None)
    top_k = _override("top_k", cfg.top_k, int)
    bm25_weight = _override("bm25_weight", cfg.bm25_weight, float)
    bm25_k = _override("bm25_k", cfg.bm25_k, int)
    vector_k = _override("vector_k", cfg.vector_k, int)
    use_reranker = bool(data.get("use_reranker", False))

    try:
        rag, rebuilt = services.get_rag(chunk_size, chunk_overlap, separators)
    except Exception as e:
        return jsonify({"error": f"索引构建失败: {e}"}), 500

    if rag is None:
        return jsonify({"error": "文档库为空，请先上传文档并清洗入库"}), 400

    reranker = None
    if use_reranker:
        try:
            reranker = services.get_reranker()
            if reranker is None:
                return jsonify({"error": "重排序不可用：未找到 API Key"}), 400
        except Exception as e:
            return jsonify({"error": f"重排序初始化失败: {e}"}), 500

    try:
        fetch_k = max(top_k, bm25_k, vector_k)
        hits = rag.search(
            q,
            top_k=fetch_k,
            bm25_k=bm25_k,
            vector_k=vector_k,
            bm25_weight=bm25_weight,
            reranker=reranker,
        )
        hits = services.apply_source_weights(hits, use_reranker=use_reranker)[:top_k]
    except Exception as e:
        return jsonify({"error": f"检索失败: {e}"}), 500

    return jsonify({"hits": hits, "rebuilt": rebuilt, "source_weights": services.current_source_weights()})


# ── 向量库维护 ────────────────────────────────────────────────────────────────
@bp.route("/vectordb/clear", methods=["POST"])
def vectordb_clear():
    """删除当前向量集合（不影响 docs/ 中的原文件），并失效 RAG 缓存。"""
    try:
        cfg = services.load_config()
        persist_dir = Path(cfg.persist_directory)
        if not persist_dir.is_absolute():
            persist_dir = CONFIG_PATH.parent / persist_dir

        deleted = 0
        collection_existed = False
        if persist_dir.exists():
            chroma = chromadb.Client(
                _ChromaSettings(is_persistent=True, persist_directory=str(persist_dir))
            )
            names = [c.name for c in chroma.list_collections()]
            if cfg.collection_name in names:
                collection_existed = True
                col = chroma.get_collection(cfg.collection_name)
                try:
                    deleted = col.count()
                except Exception:
                    deleted = 0
                chroma.delete_collection(cfg.collection_name)

        services.invalidate_rag()
        return jsonify({
            "ok": True,
            "deleted": deleted,
            "collection_existed": collection_existed,
            "persist_dir": str(persist_dir),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 向量库重建（SSE）─────────────────────────────────────────────────────────────
@bp.route("/vectordb/rebuild", methods=["POST"])
def vectordb_rebuild():
    """用当前 embedding 配置对所有已有文档重建向量库（SSE 流式进度）。
    通常在 embedding 模型更换后自动调用，也可手动触发。
    """
    def generate():
        try:
            yield f"data: {json.dumps({'type':'status','message':'正在重建向量库…'}, ensure_ascii=False)}\n\n"

            services.invalidate_rag()

            cfg = services.load_config()
            chunk_size    = cfg.chunk_size
            chunk_overlap = cfg.chunk_overlap

            rag, rebuilt = services.get_rag(chunk_size, chunk_overlap, None)

            if rag is None:
                yield f"data: {json.dumps({'type':'done','rebuilt':False,'count':0,'message':'文档库为空，无文档可重建'}, ensure_ascii=False)}\n\n"
                return

            count = len(rag.documents)
            yield f"data: {json.dumps({'type':'done','rebuilt':rebuilt,'count':count,'message':f'向量库重建完成，共 {count} 个文档块'}, ensure_ascii=False)}\n\n"

        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','message':str(exc)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 清洗入库（SSE）─────────────────────────────────────────────────────────────
@bp.route("/ingest", methods=["POST"])
def ingest():
    """对 docs/ 中已有文件执行 qwen3-max 清洗 → 嵌入 → 存入 Chroma。"""
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "filename 不能为空"}), 400
    target = DOCS_DIR / Path(filename).name
    if not target.is_file():
        return jsonify({"error": f"文件不存在: {filename}"}), 404

    def generate():
        try:
            # Step 1: 读取（仍在 api 层做，因为需要 raw_preview）
            # PDF/Word 走 extract_text_from_file 提取文本，纯文本类按 UTF-8 读取
            yield f"data: {json.dumps({'type':'reading','message':'读取文件内容...'}, ensure_ascii=False)}\n\n"
            try:
                raw = extract_text_from_file(target).strip()
            except Exception as exc:
                yield f"data: {json.dumps({'type':'error','message':f'读取文件失败：{exc}'}, ensure_ascii=False)}\n\n"
                return
            if not raw:
                yield f"data: {json.dumps({'type':'error','message':'文件为空或未提取到文本（扫描版 PDF 需 OCR）'}, ensure_ascii=False)}\n\n"
                return

            # Step 2: 清洗（走清洗子图）
            cleaner_cfg = services.load_cleaner_settings()
            if not cleaner_cfg["api_key"]:
                yield f"data: {json.dumps({'type':'error','message':'未配置 API Key'}, ensure_ascii=False)}\n\n"
                return
            msg = f"调用 {cleaner_cfg['model_name']} 清洗（原始 {len(raw)} 字）..."
            yield f"data: {json.dumps({'type':'cleaning','message':msg}, ensure_ascii=False)}\n\n"

            cleaning_graph = build_cleaning_graph()
            out = cleaning_graph.invoke({
                "raw_text": raw,
                "system_prompt": _CLEAN_SYSTEM,
                "cleaner_cfg": cleaner_cfg,
            })
            if out.get("error"):
                yield f"data: {json.dumps({'type':'error','message':out['error']}, ensure_ascii=False)}\n\n"
                return
            cleaned = (out.get("cleaned_text") or "").strip()
            cfg = services.load_config()

            if not cleaned:
                result = {
                    "type": "result",
                    "raw_preview": raw[:800],
                    "cleaned_content": "（内容全为闲聊，已完全过滤）",
                    "chunks_stored": 0,
                    "raw_len": len(raw),
                    "clean_len": 0,
                }
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                return

            # Step 3: 落盘清洗文本 + 失效缓存（不再单独写 Chroma）
            #
            # 实际的嵌入/索引交给 services.get_rag() 的统一重建（_rebuild_rag，带按 chunk 哈希的
            # 嵌入缓存）。这里**不再**手动 embed + col.add()——那套写入会被下一次 _rebuild_rag
            # 的整集合重建覆盖（ChromaVectorStore 每次删集合重建），属无效功。详见
            # write_skill/references/common-pitfalls 「ingest 写库被重建覆盖」。
            msg = f"落盘清洗结果并刷新索引（清洗后 {len(cleaned)} 字）..."
            yield f"data: {json.dumps({'type':'storing','message':msg}, ensure_ascii=False)}\n\n"

            # 将清洗后内容覆写原文件（仅纯文本类，loader 下次读到的即清洗文本）；
            # PDF/Word 二进制原件保留供 read_document，其检索文本由 loader 在重建时结构化提取。
            if target.suffix.lower() not in _BINARY_DOC_EXT:
                target.write_text(cleaned, encoding="utf-8")

            services.invalidate_rag()

            # chunks_stored 反映「将被索引的分块数」估算（统一重建时以 loader 提取文本为准，
            # 二进制文档可能与此略有出入；正式文档清洗多为原样透传，二者基本一致）。
            chunks = DocumentChunker.chunk(cleaned, cfg.chunk_size, cfg.chunk_overlap)

            result = {
                "type": "result",
                "raw_preview": raw[:800],
                "cleaned_content": cleaned,
                "chunks_stored": len(chunks),
                "raw_len": len(raw),
                "clean_len": len(cleaned),
            }
            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"

        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','message':str(exc)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
