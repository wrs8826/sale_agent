"""Agent 对话蓝图：QA 主图流式对话 + 反馈走清洗子图写入 wiki/。"""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, session, stream_with_context

from agent_service.graph import build_cleaning_graph, build_qa_graph
from agent_service.skill_loader import detect_skill, build_skill_table

from . import conversations as conv_store
from . import services
from . import conv_stats

bp = Blueprint("agent", __name__)

_COMPACT_CMDS = {"compact", "/compact"}

# generate_word_document 工具结果里下载链接的形态：[显示名](/download/文件名.docx)
_DOWNLOAD_RE = re.compile(r"/download/(?P<file>[^\s\)\]]+\.docx)")


def _extract_download(tool_result: str):
    """从 generate_word_document 的工具结果中抽取下载信息。

    Returns: {"url": "/download/...", "filename": "..."} 或 None。
    供 SSE `download` 事件与前端下载按钮使用，使下载不依赖模型转述链接。
    """
    from urllib.parse import unquote
    m = _DOWNLOAD_RE.search(tool_result or "")
    if not m:
        return None
    url = m.group(0)
    return {"url": url, "filename": unquote(m.group("file"))}

_FEEDBACK_SYSTEM = (
    "你是一个对话清洗助手。请从下方知识库问答对话中提取可作为知识库素材的事实信息。\n"
    "\n"
    "输入包含：\n"
    "  · 用户评分（1-5 分）与评语\n"
    "  · 用户与助手的多轮对话\n"
    "\n"
    "处理规则：\n"
    "  · 总结对话涉及的实质性信息（项目、需求、决策、人员、联系方式、技术要点等）\n"
    "  · 若评语指出助手回答有误或补充了正确信息，以「用户的评语」为准重写为陈述事实\n"
    "  · 评分较低（1-2 分）通常意味助手回答不准确，更应重视评语中的纠正信息\n"
    "  · 输出格式为简洁的陈述性段落（便于检索），不要保留「用户/助手」对话标签\n"
    "  · 若整段对话无可保留价值，返回空字符串\n"
    "\n"
    "只输出清洗后的纯文本，不加任何解释或前后缀。"
)


def _compact_response(conversation_id: str, user_id: int) -> Response:
    """SSE 包装：跑一次 L3 手动压缩（compact 命令），compact_done 事件回报结果。

    L3 保留尾部按"轮"且对齐发送窗口（conv_store.L3_KEEP_TAIL_TURNS）；手动与自动同为 L3，
    仅触发方式不同。会话短于保留窗口时返回 unchanged。
    """
    def gen():
        try:
            yield f"data: {json.dumps({'type':'status','message':'正在压缩对话历史…'}, ensure_ascii=False)}\n\n"
            cleaner_cfg = services.load_cleaner_settings()
            if not cleaner_cfg["api_key"]:
                yield f"data: {json.dumps({'type':'error','message':'未配置 API Key'}, ensure_ascii=False)}\n\n"
                return
            res = conv_store.compact_conversation(
                conversation_id, user_id, cleaner_cfg,
                keep_tail_turns=conv_store.L3_KEEP_TAIL_TURNS,
            )
            if res.get("unchanged"):
                payload = {"type": "compact_done", "level": 3, "unchanged": True, "reason": res.get("reason", "")}
            elif res.get("error"):
                payload = {"type": "error", "message": res["error"]}
            else:
                payload = {
                    "type": "compact_done",
                    "level": 3,
                    "compacted_count": res["compacted_count"],
                    "kept_count": res["kept_count"],
                    "summary_preview": (res["summary"] or "")[:200],
                }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','message':str(exc)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/agent/chat", methods=["POST"])
def agent_chat():
    """SSE 流式端点：驱动 QA 主图，节点自带 tool_*/token/done 事件。"""
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    top_k = int(data.get("top_k", 5))
    conversation_id = (data.get("conversation_id") or "").strip()

    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    # 获取当前登录用户 ID（未登录时拒绝持久化但允许匿名对话）
    user_id: int | None = session.get("user_id")
    if user_id is not None:
        user_id = int(user_id)
    is_admin = session.get("role") == "admin"

    # ── 一级压缩：用户输入 compact / /compact 命令 ─────────────────────────
    if message.lower() in _COMPACT_CMDS:
        if not conversation_id:
            return jsonify({"error": "compact 命令需要在会话内使用"}), 400
        if user_id is None:
            return jsonify({"error": "未登录，无法执行压缩"}), 401
        # 验证会话归属
        conv = (conv_store.find_conversation(conversation_id) if is_admin
                else conv_store.load_conversation(conversation_id, user_id))
        if conv is None:
            return jsonify({"error": "会话不存在或已被删除"}), 404
        owner_id = int(conv.get("user_id") or user_id)
        return _compact_response(conversation_id, owner_id)

    # conversation_id 提供时，以服务端持久化的历史为准（多会话隔离的关键）
    if conversation_id and user_id is not None:
        conv = (conv_store.find_conversation(conversation_id) if is_admin
                else conv_store.load_conversation(conversation_id, user_id))
        if conv is None:
            return jsonify({"error": "会话不存在或已被删除"}), 404
        if not is_admin and conv.get("user_id") != user_id:
            return jsonify({"error": "无权访问该会话"}), 403
        # admin 只能查看他人会话，不能往里发消息
        if is_admin and conv.get("user_id") != user_id:
            return jsonify({"error": "管理员只能查看用户对话历史，不能代用户发送消息"}), 403
        owner_id = int(conv.get("user_id") or user_id)
        history = conv_store.get_history(conversation_id, owner_id)
    elif user_id is not None:
        # 未提供 conversation_id 且已登录 → 自动创建新会话
        now = conv_store._now()
        new_conv = {
            "id": conv_store.new_id(),
            "user_id": user_id,
            "title": conv_store._DEFAULT_TITLE,
            "created_at": now,
            "updated_at": now,
            "summary": "",
            "compact_at": 0,
            "messages": [],
        }
        conv_store.save_conversation(new_conv)
        conversation_id = new_conv["id"]
        owner_id = user_id
        history = []
    else:
        # 未登录匿名对话：不持久化
        owner_id = None
        history = data.get("history", []) or []

    chat_cfg = services.load_chat_settings()
    if not chat_cfg["api_key"]:
        return jsonify({"error": "未配置 Chat API Key，请在右侧设置中填写"}), 400

    # 首次对话也能自动构建 RAG（不依赖管理员先跑 /query）
    try:
        cfg = services.load_config()
        rag, _ = services.get_rag(
            cfg.chunk_size,
            cfg.chunk_overlap,
            list(cfg.separators) if cfg.separators else None,
        )
    except Exception as e:
        return jsonify({"error": f"索引构建失败: {e}"}), 500

    def rag_fn(q: str, k: int):
        cur = services.get_current_rag()
        if cur is None:
            return []
        try:
            return cur.search(q, top_k=k)
        except Exception:
            return []

    use_rag = rag is not None

    agent_mode = services.get_agent_mode()    # feature flag：react 多步循环 / single 单趟
    try:
        graph = build_qa_graph(agent_mode)
    except Exception as e:
        return jsonify({"error": f"QA 图初始化失败: {e}"}), 500

    skill = detect_skill(message)
    state = {
        "query": message,
        "history": history,
        "chat_cfg": chat_cfg,
        "rag_fn": rag_fn if use_rag else None,
        "top_k": top_k,
        "score_threshold": services.load_rag_threshold(),
        "skill_system_prompt": skill.system_prompt if skill else None,
        "skill_table": build_skill_table(),   # L1 常驻注入
        "web_tools": True,                    # 启用网页端专属工具（文档读取）；飞书路径不带此标志
        "agent_mode": agent_mode,
        "max_tool_rounds": 15,                # react 模式最大工具调用轮数
        "enable_planning": services.get_plan_first(),  # react：执行前先列方案（任务拆分）
    }

    def generate():
        full_text = ""

        # ── 二级压缩：估算 token，超 80% 上限自动压缩历史 ──────────────────
        if conversation_id and owner_id is not None:
            threshold = int(conv_store.MAX_CONTEXT_TOKENS * conv_store.COMPACT_THRESHOLD)
            tokens = conv_store.estimate_history_tokens(state["history"]) + conv_store.estimate_tokens(message)
            if tokens > threshold:
                # L4 熔断：本次若是第 CIRCUIT_BREAK_AFTER 次自动压缩，则改为全局强压（keep_tail=0）
                prior = conv_stats.get_compact_count(owner_id, conversation_id)
                is_circuit = (prior + 1) >= conv_store.CIRCUIT_BREAK_AFTER
                stage = "熔断·全局强制压缩" if is_circuit else "自动压缩"
                yield (
                    "data: " + json.dumps({
                        "type": "status",
                        "message": f"对话历史约 {tokens} tokens，超过 {int(conv_store.COMPACT_THRESHOLD*100)}% 阈值，正在{stage}…",
                    }, ensure_ascii=False) + "\n\n"
                )
                cleaner_cfg = services.load_cleaner_settings()
                keep = conv_store.L4_KEEP_TAIL_TURNS if is_circuit else conv_store.L3_KEEP_TAIL_TURNS
                res = conv_store.compact_conversation(
                    conversation_id, owner_id, cleaner_cfg, keep_tail_turns=keep,
                )
                if res.get("ok"):
                    new_history = conv_store.get_history(conversation_id, owner_id)
                    state["history"] = new_history
                    tokens_after = conv_store.estimate_history_tokens(new_history) + conv_store.estimate_tokens(message)
                    if is_circuit:
                        # 熔断后持久化清零计数（DB 写，刷新/重启不丢）
                        conv_stats.reset_compact_count(owner_id, conversation_id)
                        yield (
                            "data: " + json.dumps({
                                "type": "circuit_break",
                                "compacted_count": res["compacted_count"],
                                "kept_count": res["kept_count"],
                                "summary_preview": res["summary"][:200],
                                "tokens_before": tokens,
                                "tokens_after": tokens_after,
                                "total_compact_count": 0,  # 已清零
                            }, ensure_ascii=False) + "\n\n"
                        )
                    else:
                        # 写库：压缩次数 +1，并取回最新值随事件下发给前端
                        total_count = conv_stats.increment_compact_count(owner_id, conversation_id)
                        yield (
                            "data: " + json.dumps({
                                "type": "auto_compacted",
                                "level": 3,
                                "compacted_count": res["compacted_count"],
                                "kept_count": res["kept_count"],
                                "summary_preview": res["summary"][:200],
                                "tokens_before": tokens,
                                "tokens_after": tokens_after,
                                "total_compact_count": total_count,  # 累计压缩次数（供前端判断提示）
                            }, ensure_ascii=False) + "\n\n"
                        )
                elif res.get("unchanged"):
                    pass  # 太短无法压缩，继续即可
                else:
                    yield (
                        "data: " + json.dumps({
                            "type": "warning",
                            "message": f"自动压缩失败：{res.get('error')}，继续使用原历史",
                        }, ensure_ascii=False) + "\n\n"
                    )

        # 发送态裁剪（L1 窗口 + L2 工具裁剪）：在 token 估算/自动压缩之后、真正发模型之前
        # 只影响"这一轮发什么"，不动存储；UI 仍从完整 messages[] 渲染
        state["history"] = conv_store.window_history(state["history"])

        tool_items_acc: list = []   # 本轮工具调用记录，用于持久化（Phase 0 工具轮持久化）
        try:
            for event in graph.stream(state, stream_mode="custom"):
                # stream_mode="custom" 时 event 就是节点 writer 推出的 dict
                etype = event.get("type")
                if etype == "done":
                    full_text = event.get("full_text") or ""
                elif etype == "tool_turn":
                    tool_items_acc = event.get("items") or tool_items_acc
                    continue  # 内部事件，不下发前端
                elif etype == "tool_end" and event.get("name") == "generate_word_document":
                    # 确定性下载：从真实工具结果里抽出 /download/xxx.docx，下发结构化 download 事件，
                    # 前端据此渲染下载按钮——不依赖模型把链接原样转述（模型常写错/编造链接）。
                    dl = _extract_download(str(event.get("result") or ""))
                    if dl:
                        yield f"data: {json.dumps({'type': 'download', **dl}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return

        # 持久化：仅 conversation_id 有效且 done 拿到回复时落盘
        if conversation_id and owner_id is not None and full_text:
            try:
                updated = conv_store.append_turn(conversation_id, owner_id, message, full_text, tool_items_acc)
                if updated is not None:
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "conversation_saved",
                                "conversation_id": conversation_id,
                                "title": updated.get("title"),
                                "updated_at": updated.get("updated_at"),
                                "message_count": len(updated.get("messages") or []),
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
            except Exception as exc:
                yield f"data: {json.dumps({'type':'error','message':f'会话落盘失败: {exc}'}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/feedback", methods=["POST"])
def feedback():
    """评分+评语 → 清洗子图（feedback prompt）→ 写 wiki/ → 失效 RAG 缓存。"""
    data = request.get_json(silent=True) or {}
    rating = int(data.get("rating", 0))
    comment = (data.get("comment") or "").strip()
    history = data.get("history", [])
    conversation_id = (data.get("conversation_id") or "").strip()
    # 从 session 读取用户名，未登录时用 anonymous
    username = re.sub(r"[^\w]", "_", session.get("username") or "anonymous")

    if not history:
        return jsonify({"error": "对话历史为空"}), 400
    if rating < 1 or rating > 5:
        return jsonify({"error": "评分必须在 1-5 之间"}), 400

    def generate():
        try:
            yield f"data: {json.dumps({'type':'status','message':'整理对话历史…'}, ensure_ascii=False)}\n\n"
            conv_text = "\n".join(
                f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content','')}"
                for m in history
            )
            llm_input = (
                f"用户评分：{rating}/5\n"
                f"用户评语：{comment or '（未填写）'}\n\n"
                f"对话记录：\n{conv_text}"
            )

            cleaner_cfg = services.load_cleaner_settings()
            if not cleaner_cfg["api_key"]:
                yield f"data: {json.dumps({'type':'error','message':'未配置 API Key'}, ensure_ascii=False)}\n\n"
                return
            msg = f"调用 {cleaner_cfg['model_name']} 总结清洗（对话 {len(conv_text)} 字）…"
            yield f"data: {json.dumps({'type':'status','message':msg}, ensure_ascii=False)}\n\n"

            out = build_cleaning_graph().invoke({
                "raw_text": llm_input,
                "system_prompt": _FEEDBACK_SYSTEM,
                "cleaner_cfg": cleaner_cfg,
            })
            if out.get("error"):
                yield f"data: {json.dumps({'type':'error','message':out['error']}, ensure_ascii=False)}\n\n"
                return
            cleaned = (out.get("cleaned_text") or "").strip()

            if not cleaned:
                result = {
                    "type": "result",
                    "filename": "",
                    "cleaned_preview": "",
                    "raw_len": len(conv_text),
                    "clean_len": 0,
                    "message": "本轮对话无可保留内容，未写入",
                }
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                return

            wiki_dir = services.get_wiki_dir()
            yield f"data: {json.dumps({'type':'status','message':f'写入 wiki/ …'}, ensure_ascii=False)}\n\n"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            conv_short = conversation_id[:8] if conversation_id else "noconv"
            filename = f"feedback_{username}_{conv_short}_{ts}_{rating}star.txt"
            target = wiki_dir / filename
            header = (
                f"# 对话反馈摘要\n"
                f"# 用户: {session.get('username') or 'anonymous'}\n"
                f"# 会话: {conversation_id or '（无会话）'}\n"
                f"# 评分: {rating}/5\n"
                f"# 评语: {comment or '（未填写）'}\n"
                f"# 时间: {ts}\n\n"
            )
            target.write_text(header + cleaned, encoding="utf-8")

            services.invalidate_rag()

            result = {
                "type": "result",
                "filename": filename,
                "filepath": str(target),
                "cleaned_preview": cleaned[:400],
                "raw_len": len(conv_text),
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
