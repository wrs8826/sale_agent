"""检索评测脚本：对比 BM25 / 向量 / 混合检索的召回质量与延迟。

复用生产的索引构建（api.services.get_rag），即与线上完全一致的分块、嵌入、
混合融合逻辑，因此跑出来的指标可直接写进简历/报告，面试可追问。

指标：
    Hit@1     第 1 条即命中相关文档的查询占比
    Hit@k     top-k 内命中相关文档的查询占比（召回命中率）
    Recall@k  平均「命中的相关文档数 / 标注相关文档总数」
    MRR       首个相关文档排名倒数的平均（衡量排序质量）
    延迟       每条查询的检索耗时 mean / p50 / p95（毫秒）

用法：
    # 1) 先按现有知识库生成一份标注模板（列出所有可用文件名），再手工填 relevant
    python eval/retrieval_eval.py --make-template

    # 2) 填好 eval/eval_set.json 后运行评测
    python eval/retrieval_eval.py

    # 可选项
    python eval/retrieval_eval.py --top-k 5 --modes bm25,vector,hybrid \
        --reranker --report eval/report.json --verbose

标注集格式（eval/eval_set.json）：
    [
      {"query": "差旅报销标准是多少？", "relevant": ["差旅管理制度.pdf"]},
      {"query": "试用期多久？",        "relevant": ["员工手册.docx"]}
    ]
    relevant 既可写完整文件名，也可写关键片段（对 filename / source 路径做大小写无关匹配）。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# 允许从项目根目录直接 `python eval/retrieval_eval.py` 运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_EVAL_SET = Path(__file__).resolve().parent / "eval_set.json"

# 各模式对应的 bm25_weight（混合融合时 BM25 与向量的权重）：
#   bm25   → 1.0（纯词项检索）
#   vector → 0.0（纯向量检索）
#   hybrid → 取 config.yaml 的 bm25_weight（线上实际配置）
_MODE_WEIGHTS = {"bm25": 1.0, "vector": 0.0}  # hybrid 在运行时按 config 填入


def _build_rag():
    """按线上同款逻辑构建混合检索索引，返回 (rag, config)。"""
    from api import services

    cfg = services.load_config()
    seps = list(cfg.separators) if cfg.separators else None
    rag, _ = services.get_rag(cfg.chunk_size, cfg.chunk_overlap, seps)
    if rag is None:
        sys.exit(
            "未加载到任何文档：请确认 agent_service/docs（及 wiki / skills）下有知识库文件。"
        )
    return rag, cfg


def _load_eval_set(path: Path) -> List[Dict]:
    if not path.exists():
        sys.exit(
            f"标注集不存在：{path}\n请先运行  python eval/retrieval_eval.py --make-template  生成模板。"
        )
    with path.open("r", encoding="utf-8") as r:
        data = json.load(r)
    items: List[Dict] = []
    for i, row in enumerate(data):
        query = (row.get("query") or "").strip()
        rel = row.get("relevant", [])
        if isinstance(rel, str):
            rel = [rel]
        rel = [str(x).strip() for x in rel if str(x).strip()]
        if not query or not rel:
            print(f"  ! 跳过第 {i} 条：缺少 query 或 relevant")
            continue
        items.append({"query": query, "relevant": rel})
    if not items:
        sys.exit("标注集为空或全部无效。")
    return items


def _make_template(rag, out: Path) -> None:
    """扫描已建索引的全部来源文件，生成一份带候选文件名清单的标注模板。"""
    files = sorted({(m or {}).get("filename", "") for m in rag.metadatas if m})
    files = [f for f in files if f]
    template = {
        "_available_files": files,
        "_note": "把下面每条的 query 改成真实问题，relevant 填上面 _available_files 里应命中的文件名（可多个）。填完删除以 _ 开头的辅助字段。",
        "samples": [
            {"query": "（在此填写真实问题 1）", "relevant": [files[0] if files else "文件名.pdf"]},
            {"query": "（在此填写真实问题 2）", "relevant": [files[1] if len(files) > 1 else "文件名.docx"]},
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as w:
        json.dump(template, w, ensure_ascii=False, indent=2)
    print(f"已写出模板：{out}")
    print(f"知识库共 {len(files)} 个文件：")
    for f in files:
        print(f"  - {f}")
    print("\n请把模板里的 query/relevant 填成真实问答，并把外层数组改成纯列表（见脚本顶部格式说明）后再评测。")


def _is_relevant(hit: Dict, relevant: List[str]) -> bool:
    md = hit.get("metadata") or {}
    fn = str(md.get("filename", "")).lower()
    # source 路径分隔符归一化为 /，使标注里可用 "城市/references/文件名.md" 这种
    # 唯一路径精确命中（不受 Windows \ 与 Linux / 差异影响）。
    src = str(md.get("source", "")).lower().replace("\\", "/")
    for r in relevant:
        rl = r.lower().replace("\\", "/")
        if rl and (rl == fn or rl in fn or rl in src):
            return True
    return False


def _eval_mode(
    rag,
    items: List[Dict],
    bm25_weight: float,
    top_k: int,
    reranker,
    verbose: bool,
) -> Dict:
    n = len(items)
    hit1 = hitk = 0
    rr_sum = 0.0
    recall_sum = 0.0
    latencies: List[float] = []

    for item in items:
        query, relevant = item["query"], item["relevant"]
        t0 = time.perf_counter()
        hits = rag.search(query, top_k=top_k, bm25_weight=bm25_weight, reranker=reranker)
        latencies.append((time.perf_counter() - t0) * 1000.0)

        flags = [_is_relevant(h, relevant) for h in hits]
        first_rank = next((i + 1 for i, ok in enumerate(flags) if ok), None)
        if first_rank == 1:
            hit1 += 1
        if first_rank is not None:
            hitk += 1
            rr_sum += 1.0 / first_rank
        # 相关文档总数按标注条目数算（一条 relevant 视为一个目标文档）
        found = len({r for r in relevant for h in hits if _is_relevant(h, [r])})
        recall_sum += found / len(relevant)

        if verbose:
            mark = f"#{first_rank}" if first_rank else "miss"
            top_files = [str((h.get("metadata") or {}).get("filename", "?")) for h in hits]
            print(f"    [{mark:>5}] {query[:30]:<30} → {top_files}")

    return {
        "n": n,
        "hit@1": hit1 / n,
        "hit@k": hitk / n,
        "recall@k": recall_sum / n,
        "mrr": rr_sum / n,
        "latency_ms_mean": statistics.mean(latencies),
        "latency_ms_p50": statistics.median(latencies),
        "latency_ms_p95": (sorted(latencies)[max(0, int(0.95 * n) - 1)]),
    }


def _print_table(top_k: int, results: Dict[str, Dict]) -> None:
    cols = ["mode", "Hit@1", f"Hit@{top_k}", f"Recall@{top_k}", "MRR", "lat_mean", "lat_p50", "lat_p95"]
    print("\n" + "=" * 84)
    print(f"检索评测结果（top_k={top_k}，{next(iter(results.values()))['n']} 条查询）")
    print("=" * 84)
    print("{:<8}{:>9}{:>9}{:>11}{:>8}{:>10}{:>10}{:>10}".format(*cols))
    print("-" * 84)
    for mode, m in results.items():
        print(
            "{:<8}{:>8.1%}{:>9.1%}{:>11.1%}{:>8.3f}{:>9.0f}m{:>9.0f}m{:>9.0f}m".format(
                mode, m["hit@1"], m["hit@k"], m["recall@k"], m["mrr"],
                m["latency_ms_mean"], m["latency_ms_p50"], m["latency_ms_p95"],
            )
        )
    print("=" * 84)
    # 混合 vs 向量的相对提升，方便直接写简历
    if "hybrid" in results and "vector" in results:
        hy, ve = results["hybrid"], results["vector"]
        for metric in ("hit@k", "recall@k", "mrr"):
            base = ve[metric]
            if base > 0:
                delta = (hy[metric] - base) / base * 100
                print(f"  混合 vs 纯向量  {metric:<9} 相对提升 {delta:+.1f}%  "
                      f"({base:.1%} → {hy[metric]:.1%})" if metric != "mrr"
                      else f"  混合 vs 纯向量  {metric:<9} 相对提升 {delta:+.1f}%  ({base:.3f} → {hy[metric]:.3f})")


def main() -> None:
    parser = argparse.ArgumentParser(description="检索评测：BM25 / 向量 / 混合 对比")
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET, help="标注集 JSON 路径")
    parser.add_argument("--top-k", type=int, default=None, help="检索条数（默认取 config.top_k）")
    parser.add_argument("--modes", type=str, default="bm25,vector,hybrid", help="逗号分隔：bm25,vector,hybrid")
    parser.add_argument("--reranker", action="store_true", help="对每种模式追加 reranker 重排（需配置 reranker.api_key）")
    parser.add_argument("--report", type=Path, default=None, help="把结果写出为 JSON")
    parser.add_argument("--make-template", action="store_true", help="扫描知识库生成标注模板后退出")
    parser.add_argument("--verbose", action="store_true", help="打印每条查询的命中排名")
    args = parser.parse_args()

    print("构建检索索引中（与线上同款逻辑）……")
    rag, cfg = _build_rag()
    print(f"索引就绪：{len(rag.documents)} 个分块。")

    if args.make_template:
        _make_template(rag, args.eval_set)
        return

    top_k = args.top_k or cfg.top_k
    reranker = None
    if args.reranker:
        from api import services

        reranker = services.get_reranker()
        if reranker is None:
            print("  ! 未配置 reranker.api_key，忽略 --reranker")

    items = _load_eval_set(args.eval_set)
    print(f"标注集：{len(items)} 条查询。")

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    results: Dict[str, Dict] = {}
    for mode in modes:
        if mode == "hybrid":
            weight = cfg.bm25_weight
        elif mode in _MODE_WEIGHTS:
            weight = _MODE_WEIGHTS[mode]
        else:
            print(f"  ! 未知模式 {mode}，跳过")
            continue
        print(f"\n评测模式：{mode}（bm25_weight={weight}）")
        results[mode] = _eval_mode(rag, items, weight, top_k, reranker, args.verbose)

    _print_table(top_k, results)

    if args.report:
        payload = {"top_k": top_k, "n_chunks": len(rag.documents), "modes": results}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8") as w:
            json.dump(payload, w, ensure_ascii=False, indent=2)
        print(f"\n已写出报告：{args.report}")


if __name__ == "__main__":
    main()
