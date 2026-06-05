#!/usr/bin/env python3
"""Recall evaluation: query → expected doc_ids → hit rate.

Usage:
  eval-recall.py                    # run all eval cases
  eval-recall.py --topk 10          # custom top-k

Output:
  Per-query: recall, MRR, hits list
  Summary: avg recall@k, MRR
"""

import json
import sys
import urllib.request

API = "http://localhost:8001/api/v1"

# Each case: (query, [expected_doc_id_substrings], min_expected_hits)
# expected_doc_id_substrings: doc_id must contain at least one of these
EVAL_CASES = [
    {
        "query": "nginx 磁盘满怎么处理",
        "expect_title_contains": ["磁盘满", "清理日志"],
        "description": "nginx disk full SOP",
    },
    {
        "query": "nginx 清理日志 操作步骤",
        "expect_title_contains": ["磁盘满", "清理日志"],
        "description": "nginx log cleanup steps",
    },
    {
        "query": "elasticsearch 9200端口不可达 重启",
        "expect_title_contains": ["9200", "重启"],
        "description": "ES 9200 port restart SOP",
    },
    {
        "query": "nginx 集群架构 节点信息",
        "expect_title_contains": ["nginx", "技术架构"],
        "description": "nginx cluster architecture",
    },
    {
        "query": "elasticsearch 集群节点配置",
        "expect_title_contains": ["elasticsearch", "技术架构"],
        "description": "ES cluster tech doc",
    },
    {
        "query": "nginx 部署在哪些机器上",
        "expect_title_contains": ["nginx", "技术架构"],
        "description": "nginx deployment topology",
    },
    {
        "query": "ERP K3Cloud 架构 依赖哪些服务",
        "expect_title_contains": ["K3Cloud", "ERP"],
        "description": "ERP architecture (may miss)",
    },
    {
        "query": "nginx 502 排查",
        "expect_title_contains": ["nginx"],
        "description": "nginx 502 troubleshooting",
    },
]


def search(query: str, topk: int = 10) -> list[dict]:
    data = json.dumps({"query": query, "top_k": topk}).encode()
    req = urllib.request.Request(
        f"{API}/query",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=60)
    return json.loads(resp.read()).get("sources", [])


def check_hit(source: dict, expects: list[str]) -> bool:
    """Check if source title or snippet matches any expected term."""
    title = source.get("title", "").lower()
    snippet = source.get("snippet", source.get("content", "")).lower()
    text = title + " " + snippet
    for exp in expects:
        if exp.lower() in text:
            return True
    return False


def recall_at_k(sources: list[dict], expects: list[str], k: int) -> tuple[int, float, float]:
    """Returns (hits, recall, MRR).
    recall = hits / min(k, len(expects)) — capped at k if expects > k."""
    hits = 0
    first_rank = None
    for i, s in enumerate(sources[:k]):
        if check_hit(s, expects):
            hits += 1
            if first_rank is None:
                first_rank = i + 1

    # recall@k: hits out of expected (cap at k)
    max_hits = min(k, len(expects)) if expects else k
    rec = hits / max_hits if max_hits > 0 else 0
    mrr = 1.0 / first_rank if first_rank else 0
    return hits, rec, mrr


def main(topk: int = 10):
    total_recall = 0
    total_mrr = 0
    n = 0

    print(f"{'='*70}")
    print(f"  Recall Evaluation (top-{topk})")
    print(f"{'='*70}")

    for case in EVAL_CASES:
        n += 1
        desc = case["description"]
        expects = case["expect_title_contains"]
        try:
            sources = search(case["query"], topk)
            hits, rec, mrr = recall_at_k(sources, expects, topk)
            total_recall += rec
            total_mrr += mrr

            # Display
            flag = "✅" if rec >= 0.5 else ("⚠️" if rec > 0 else "❌")
            print(f"\n{flag} [{desc}] recall@{topk}={rec:.1%}  MRR={mrr:.3f}")
            print(f"   Query: {case['query']}")
            print(f"   Expected: {expects}")
            print(f"   Returned (top-{min(5, len(sources))}):")
            for i, s in enumerate(sources[:5]):
                hit_mark = "← HIT" if check_hit(s, expects) else ""
                print(f"     {i+1}. [{s.get('confidence','?')}] {s.get('title','?')[:50]} {hit_mark}")
        except Exception as e:
            print(f"\n❌ [{desc}] ERROR: {e}")

    avg_rec = total_recall / n if n else 0
    avg_mrr = total_mrr / n if n else 0
    print(f"\n{'='*70}")
    print(f"  SUMMARY: avg recall@{topk}={avg_rec:.1%}  avg MRR={avg_mrr:.3f}  ({n} queries)")
    print(f"{'='*70}")


if __name__ == "__main__":
    topk = int(sys.argv[2]) if "--topk" in sys.argv and len(sys.argv) > 2 else 10
    main(topk)
