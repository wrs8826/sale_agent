"""端到端 Agent 评测：检索召回 / 置信度 / 工具执行 / 答案关键词命中 / 忠实度。

与 retrieval_eval.py（只评检索）不同，本脚本真正驱动 QA 主图（与线上同款
build_qa_graph + graph.stream），因此能评测"工具选得对不对""回答有没有
幻觉"等只有跑通 Agent 才测得出的指标。

评测维度：
    检索召回   Recall@k / MRR / Hit@1（正确文档有没有被捞回、排得靠不靠前）
    置信度     top-1 检索分数与 rag_score_threshold 的关系（域内应高、域外应低于阈值回退）
    工具执行   expected_tool 是否被正确调用（工具选择准确率）+ 工具报错率
    关键词命中 答案是否包含 must_contain 关键事实（不依赖 LLM 的廉价正确性代理）
    忠实度     LLM-as-judge：回答是否被检索上下文/工具结果支持（faithfulness）+ 相关性

依赖：检索/置信度只需 embedding；工具/关键词/忠实度需 chat API Key
（config.yaml 的 chat 段）。缺 Key 时自动跳过需要 Agent 的维度。

用法：
    python eval/agent_eval.py --make-template     # 生成标注模板（列出知识库文件）
    python eval/agent_eval.py                      # 全量评测
    python eval/agent_eval.py --no-judge           # 跳过忠实度 LLM 评判（省 token）
    python eval/agent_eval.py --retrieval-only     # 只评检索+置信度（无需 chat Key）
    python eval/agent_eval.py --limit 10 --verbose # 只跑前 10 条并打印细节

标注集格式（eval/agent_eval_set.json）：
    [
      {
        "query": "刘志强代缴社保服务费多少？",
        "category": "rag",                       # rag | tool | policy | ood（可选，用于分组）
        "relevant": ["私聊_刘志强（一年社保医保公积金）.txt"],   # 检索召回的 ground truth（可选）
        "expected_tool": "load_policy_file",     # 期望调用的工具（可选）
        "must_contain": ["100"]                  # 答案应包含的关键事实（可选）
      }
    ]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_EVAL_SET = Path(__file__).resolve().parent / "agent_eval_set.json"

# 真实可执行工具（区别于图内"提取关键词""检索知识库"等伪工具事件）
REAL_TOOLS = {
    "get_current_time",
    "load_policy_file",
    "generate_word_document",
    "read_document",
    "list_documents",
}


# ── 构建 RAG / rag_fn（与 api/agent.py 同款口径）──────────────────────────────
def _build_rag():
    from api import services

    cfg = services.load_config()
    seps = list(cfg.separators) if cfg.separators else None
    rag, _ = services.get_rag(cfg.chunk_size, cfg.chunk_overlap, seps)
    if rag is None:
        sys.exit("未加载到任何文档：请确认 agent_service/docs / wiki 下有知识库文件。")
    return rag, cfg


def _make_rag_fn(cfg):
    """复刻 api/agent.py 的 rag_fn：放宽召回 → reranker/来源加权 → 截到 k。"""
    from api import services

    def rag_fn(q: str, k: int):
        cur = services.get_current_rag()
        if cur is None:
            return []
        try:
            reranker = services.get_reranker()
        except Exception:
            reranker = None
        use_reranker = reranker is not None
        try:
            fetch_k = max(k, cfg.bm25_k, cfg.vector_k)
            hits = cur.search(
                q, top_k=fetch_k, bm25_k=cfg.bm25_k, vector_k=cfg.vector_k,
                bm25_weight=cfg.bm25_weight, reranker=reranker,
            )
            return services.apply_source_weights(hits, use_reranker=use_reranker)[:k]
        except Exception:
            return []

    return rag_fn


# ── 标注集 ────────────────────────────────────────────────────────────────────
def _load_eval_set(path: Path) -> List[Dict]:
    if not path.exists():
        sys.exit(f"标注集不存在：{path}\n先运行 python eval/agent_eval.py --make-template 生成模板。")
    data = json.load(path.open("r", encoding="utf-8"))
    items = []
    for row in data:
        q = (row.get("query") or "").strip()
        if not q:
            continue
        rel = row.get("relevant", [])
        rel = [rel] if isinstance(rel, str) else list(rel)
        mc = row.get("must_contain", [])
        mc = [mc] if isinstance(mc, str) else list(mc)
        items.append({
            "query": q,
            "category": row.get("category", ""),
            "relevant": [str(x).strip() for x in rel if str(x).strip()],
            "expected_tool": (row.get("expected_tool") or "").strip(),
            "must_contain": [str(x) for x in mc if str(x)],
        })
    if not items:
        sys.exit("标注集为空。")
    return items


def _make_template(rag, out: Path) -> None:
    files = sorted({(m or {}).get("filename", "") for m in rag.metadatas if m})
    files = [f for f in files if f]
    template = [
        {"query": "（RAG 召回示例：问某客户/文档里的事实）", "category": "rag",
         "relevant": [files[0] if files else "文件名.txt"], "must_contain": ["关键数字或事实"]},
        {"query": "现在几点了？", "category": "tool", "expected_tool": "get_current_time"},
        {"query": "帮我生成一份产品介绍的word文档", "category": "tool", "expected_tool": "generate_word_document"},
        {"query": "今天天气怎么样？", "category": "ood"},
    ]
    out.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写出模板：{out}\n知识库可用文件（{len(files)} 个）：")
    for f in files:
        print(f"  - {f}")


# ── 命中判定（与 retrieval_eval 一致，分隔符归一化）──────────────────────────
def _is_relevant(hit: Dict, relevant: List[str]) -> bool:
    md = hit.get("metadata") or {}
    fn = str(md.get("filename", "")).lower()
    src = str(md.get("source", "")).lower().replace("\\", "/")
    for r in relevant:
        rl = r.lower().replace("\\", "/")
        if rl and (rl == fn or rl in fn or rl in src):
            return True
    return False


# ── 驱动 QA 图跑一条查询 ──────────────────────────────────────────────────────
def _run_agent(graph, query, rag_fn, chat_cfg, agent_mode, score_threshold, top_k) -> Dict:
    from agent_service.skill_loader import detect_skill, build_skill_table

    skill = detect_skill(query)
    state = {
        "query": query,
        "history": [],
        "chat_cfg": chat_cfg,
        "rag_fn": rag_fn,
        "top_k": top_k,
        "score_threshold": score_threshold,
        "skill_system_prompt": skill.system_prompt if skill else None,
        "skill_table": build_skill_table(),
        "web_tools": True,
        "agent_mode": agent_mode,
        "max_tool_rounds": 15,
        "enable_planning": False,  # 评测关闭"先列方案"以减少噪声/耗时
    }
    answer, tools, tool_results, err = "", [], [], None
    t0 = time.perf_counter()
    try:
        for ev in graph.stream(state, stream_mode="custom"):
            t = ev.get("type")
            name = ev.get("name", "")
            if t == "tool_start" and name in REAL_TOOLS:
                tools.append(name)
            elif t == "tool_end" and name in REAL_TOOLS:
                tool_results.append({
                    "name": name,
                    "result": str(ev.get("result") or ""),
                    "error": ev.get("error"),
                })
            elif t == "done":
                answer = ev.get("full_text") or ""
            elif t == "error":
                err = ev.get("message")
    except Exception as exc:
        err = str(exc)
    latency = (time.perf_counter() - t0) * 1000.0
    return {"answer": answer, "tools": tools, "tool_results": tool_results,
            "error": err, "latency_ms": latency}


# ── 忠实度 LLM 评判 ───────────────────────────────────────────────────────────
_JUDGE_PROMPT = """你是严格的回答质量评审。请只依据【上下文】判断【回答】：
1) faithfulness（忠实度）：回答中的事实是否都能在上下文中找到支持，没有编造。1=完全有据，0=明显编造。
2) relevance（相关性）：回答是否切题回应了【问题】。1=完全切题，0=答非所问。
只输出 JSON：{"faithfulness": 0.x, "relevance": 0.x, "reason": "一句话"}，不要其他内容。

【问题】%s

【上下文】
%s

【回答】
%s"""


def _judge_faithfulness(client, model, query, context, answer) -> Optional[Dict]:
    ctx = (context or "（无检索上下文）")[:4000]
    ans = (answer or "").strip()[:2000]
    if not ans:
        return None
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _JUDGE_PROMPT % (query, ctx, ans)}],
            temperature=0,
        )
        text = resp.choices[0].message.content or ""
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(text[start:end + 1])
            return {
                "faithfulness": float(obj.get("faithfulness", 0)),
                "relevance": float(obj.get("relevance", 0)),
                "reason": str(obj.get("reason", "")),
            }
    except Exception as exc:
        return {"faithfulness": None, "relevance": None, "reason": f"判定失败: {exc}"}
    return None


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="端到端 Agent 评测：召回/置信度/工具/忠实度")
    ap.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET)
    ap.add_argument("--top-k", type=int, default=None, help="检索条数（默认取 config.top_k）")
    ap.add_argument("--retrieval-only", action="store_true", help="只评检索+置信度（无需 chat Key）")
    ap.add_argument("--no-judge", action="store_true", help="跳过忠实度 LLM 评判")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    ap.add_argument("--make-template", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    from api import services

    print("构建检索索引中（与线上同款）……")
    rag, cfg = _build_rag()
    print(f"索引就绪：{len(rag.documents)} 个分块。")

    if args.make_template:
        _make_template(rag, args.eval_set)
        return

    top_k = args.top_k or cfg.top_k
    score_threshold = services.load_rag_threshold()
    rag_fn = _make_rag_fn(cfg)
    items = _load_eval_set(args.eval_set)
    if args.limit:
        items = items[: args.limit]
    print(f"标注集：{len(items)} 条查询，top_k={top_k}，score_threshold={score_threshold}。")

    # 是否具备跑 Agent 的条件
    chat_cfg = services.load_chat_settings()
    run_agent = not args.retrieval_only and bool(chat_cfg["api_key"])
    if not args.retrieval_only and not chat_cfg["api_key"]:
        print("  ! 未配置 chat API Key，自动降级为 --retrieval-only（工具/关键词/忠实度跳过）")

    graph = None
    judge_client = None
    agent_mode = None
    if run_agent:
        agent_mode = services.get_agent_mode()
        from agent_service.graph import build_qa_graph
        graph = build_qa_graph(agent_mode)
        print(f"Agent 模式：{agent_mode}")
        if not args.no_judge:
            try:
                from openai import OpenAI
                judge_client = OpenAI(api_key=chat_cfg["api_key"], base_url=chat_cfg["base_url"] or None)
            except Exception as exc:
                print(f"  ! 忠实度评判初始化失败，跳过：{exc}")

    # ── 累加器 ───────────────────────────────────────────────────────────────
    R = {"n": 0, "hit1": 0, "hitk": 0, "rr": 0.0, "recall": 0.0}      # 检索
    C = {"in_scores": [], "in_pass": 0, "in_n": 0, "ood_scores": []}  # 置信度
    T = {"n": 0, "correct": 0, "errors": 0, "calls": 0, "ran": 0}     # 工具
    K = {"n": 0, "covered": 0.0}                                       # 关键词命中
    F = {"faith": [], "rel": []}                                       # 忠实度
    agent_lat: List[float] = []

    for i, item in enumerate(items):
        q, rel, exp_tool, mc = item["query"], item["relevant"], item["expected_tool"], item["must_contain"]
        cat = item["category"]

        # 1) 检索 + 置信度（始终评）
        hits = rag_fn(q, top_k)
        top_score = float(hits[0].get("hybrid_score", 0.0)) if hits else 0.0
        if cat == "ood":
            C["ood_scores"].append(top_score)
        else:
            C["in_scores"].append(top_score)
            C["in_n"] += 1
            if top_score >= score_threshold:
                C["in_pass"] += 1

        if rel:
            R["n"] += 1
            flags = [_is_relevant(h, rel) for h in hits]
            first = next((j + 1 for j, ok in enumerate(flags) if ok), None)
            if first == 1:
                R["hit1"] += 1
            if first is not None:
                R["hitk"] += 1
                R["rr"] += 1.0 / first
            found = len({r for r in rel for h in hits if _is_relevant(h, [r])})
            R["recall"] += found / len(rel)

        # 2) 跑 Agent（工具 / 关键词 / 忠实度）
        run = None
        if run_agent:
            run = _run_agent(graph, q, rag_fn, chat_cfg, agent_mode, score_threshold, top_k)
            agent_lat.append(run["latency_ms"])
            T["ran"] += 1
            T["calls"] += len(run["tools"])
            if any(tr.get("error") for tr in run["tool_results"]):
                T["errors"] += 1

            if exp_tool:
                T["n"] += 1
                if exp_tool in run["tools"]:
                    T["correct"] += 1

            if mc:
                K["n"] += 1
                hit_kw = sum(1 for s in mc if s in (run["answer"] or ""))
                K["covered"] += hit_kw / len(mc)

            # 忠实度只在域内查询上算：OOD 无知识库上下文，判官必判 0，纳入会失真
            if judge_client is not None and cat != "ood":
                ctx_parts = [h.get("text", "") for h in hits[:top_k]]
                ctx_parts += [tr["result"] for tr in run["tool_results"]]
                verdict = _judge_faithfulness(
                    judge_client, chat_cfg["model_name"], q, "\n---\n".join(ctx_parts), run["answer"]
                )
                if verdict and verdict.get("faithfulness") is not None:
                    F["faith"].append(verdict["faithfulness"])
                    F["rel"].append(verdict["relevance"])

        if args.verbose:
            line = f"[{i+1:>2}] {cat:<6} conf={top_score:.3f} {q[:28]:<28}"
            if run is not None:
                line += f" tools={run['tools']}"
                if exp_tool:
                    line += f" exp={exp_tool}{'✓' if exp_tool in run['tools'] else '✗'}"
            print(line)

    # ── 汇总输出 ─────────────────────────────────────────────────────────────
    def pct(a, b):
        return (a / b) if b else 0.0

    print("\n" + "=" * 70)
    print(f"端到端 Agent 评测（{len(items)} 条查询，top_k={top_k}）")
    print("=" * 70)

    print("\n【检索召回】（有 relevant 标注的 %d 条）" % R["n"])
    if R["n"]:
        print(f"  Hit@1   = {pct(R['hit1'], R['n']):.1%}")
        print(f"  Hit@{top_k:<3}= {pct(R['hitk'], R['n']):.1%}")
        print(f"  Recall@{top_k:<2}= {pct(R['recall'], R['n']):.1%}")
        print(f"  MRR     = {pct(R['rr'], R['n']):.3f}")

    print("\n【置信度】top-1 检索分（阈值 score_threshold=%.2f 以下应回退会话上下文）" % score_threshold)
    if C["in_n"]:
        print(f"  域内平均置信度 = {statistics.mean(C['in_scores']):.3f}（{C['in_n']} 条）")
        print(f"  域内过阈率     = {pct(C['in_pass'], C['in_n']):.1%}（≥阈值，应高）")
    if C["ood_scores"]:
        ood_mean = statistics.mean(C["ood_scores"])
        print(f"  域外平均置信度 = {ood_mean:.3f}（{len(C['ood_scores'])} 条 OOD，应低于阈值）")
        if C["in_n"]:
            print(f"  域内/域外分离度 = {statistics.mean(C['in_scores']) - ood_mean:+.3f}（越大越好）")

    if run_agent:
        print("\n【工具执行】")
        if T["n"]:
            print(f"  工具选择准确率 = {pct(T['correct'], T['n']):.1%}（{T['correct']}/{T['n']} 条期望工具被正确调用）")
        print(f"  工具报错率     = {pct(T['errors'], T['ran']):.1%}（{T['ran']} 条跑通的查询里）")
        print(f"  平均工具调用数 = {pct(T['calls'], T['ran']):.2f} 次/查询")

        print("\n【答案关键词命中】（有 must_contain 标注的 %d 条）" % K["n"])
        if K["n"]:
            print(f"  关键事实覆盖率 = {pct(K['covered'], K['n']):.1%}")

        if F["faith"]:
            print("\n【忠实度 / 相关性】（LLM-judge，域内 %d 条，已排除 OOD）" % len(F["faith"]))
            print(f"  忠实度 faithfulness = {statistics.mean(F['faith']):.3f}（1=完全有据，0=幻觉）")
            print(f"  相关性 relevance    = {statistics.mean(F['rel']):.3f}")

        if agent_lat:
            print("\n【Agent 端到端延迟】")
            print(f"  mean={statistics.mean(agent_lat):.0f}ms  p50={statistics.median(agent_lat):.0f}ms  "
                  f"p95={sorted(agent_lat)[max(0, int(0.95*len(agent_lat))-1)]:.0f}ms")
    print("=" * 70)

    if args.report:
        payload = {
            "top_k": top_k, "score_threshold": score_threshold, "n": len(items),
            "retrieval": {k: (pct(R[a], R["n"]) if k != "raw" else None)
                          for k, a in [("hit@1", "hit1"), ("hit@k", "hitk"),
                                       ("recall@k", "recall"), ("mrr", "rr")]} if R["n"] else {},
            "confidence": {
                "in_mean": statistics.mean(C["in_scores"]) if C["in_n"] else None,
                "in_pass_rate": pct(C["in_pass"], C["in_n"]) if C["in_n"] else None,
                "ood_mean": statistics.mean(C["ood_scores"]) if C["ood_scores"] else None,
            },
            "tool": {
                "selection_accuracy": pct(T["correct"], T["n"]) if T["n"] else None,
                "error_rate": pct(T["errors"], T["ran"]) if T["ran"] else None,
                "avg_calls": pct(T["calls"], T["ran"]) if T["ran"] else None,
            } if run_agent else None,
            "keyword_coverage": pct(K["covered"], K["n"]) if K["n"] else None,
            "faithfulness": statistics.mean(F["faith"]) if F["faith"] else None,
            "relevance": statistics.mean(F["rel"]) if F["rel"] else None,
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出报告：{args.report}")


if __name__ == "__main__":
    main()
