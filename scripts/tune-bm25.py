#!/usr/bin/env python3
"""BM25 parameter tuning with MRR eval. Ref: doc/ES全文检索 section 09.

Usage: python3 scripts/tune-bm25.py
"""

import sys
sys.path.insert(0, "/app")

from elasticsearch import Elasticsearch
from app.retrievers.es_retriever import INDEX_NAME


PARAM_GRID = [
    {"k1": 0.8, "b": 0.5},
    {"k1": 1.0, "b": 0.75},
    {"k1": 1.2, "b": 0.75},  # ES default
    {"k1": 1.5, "b": 0.8},
    {"k1": 2.0, "b": 0.75},
    {"k1": 0.8, "b": 0.85},
    {"k1": 1.0, "b": 0.85},
]

# Test set: query -> expected top document title keyword
EVAL_SET = [
    ("nginx 重启", "重启"),
    ("nginx 502 排查", "502"),
    ("order-service 架构", "订单"),
    ("redis 延时", "延时"),
]


def _create_temp_index(es: Elasticsearch, k1: float, b: float) -> str:
    idx = f"bm25_tune_{str(k1).replace('.','_')}_{str(b).replace('.','_')}"
    if es.indices.exists(index=idx):
        es.indices.delete(index=idx)

    es.indices.create(
        index=idx,
        body={
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "similarity": {
                        "custom_bm25": {"type": "BM25", "k1": k1, "b": b},
                    },
                },
            },
            "mappings": {
                "properties": {
                    "title": {"type": "text"},
                    "content": {"type": "text", "similarity": "custom_bm25"},
                }
            },
        },
    )

    # Copy docs from production index
    scroll = es.search(index=INDEX_NAME, body={"size": 100, "query": {"match_all": {}}}, scroll="2m")
    hits = scroll["hits"]["hits"]
    for hit in hits:
        es.index(index=idx, id=hit["_id"], document={
            "title": hit["_source"].get("title", ""),
            "content": hit["_source"].get("content", ""),
        })
    es.indices.refresh(index=idx)
    return idx


def _calculate_mrr(es: Elasticsearch, idx: str) -> float:
    total_mrr = 0.0
    for query, expected_kw in EVAL_SET:
        resp = es.search(
            index=idx,
            body={"size": 10, "query": {"match": {"content": query}}},
        )
        rank = 0
        for i, hit in enumerate(resp["hits"]["hits"], start=1):
            if expected_kw in hit["_source"].get("title", ""):
                rank = i
                break
        if rank:
            total_mrr += 1.0 / rank
    return total_mrr / len(EVAL_SET)


def main():
    from app.config import settings
    es = Elasticsearch(settings.es_url)
    if not es.ping():
        print("ES not reachable")
        sys.exit(1)

    results = []
    for params in PARAM_GRID:
        idx = _create_temp_index(es, params["k1"], params["b"])
        mrr = _calculate_mrr(es, idx)
        es.indices.delete(index=idx)
        results.append((params["k1"], params["b"], mrr))
        print(f"  k1={params['k1']:.1f}, b={params['b']:.2f} -> MRR={mrr:.4f}")

    results.sort(key=lambda x: x[2], reverse=True)
    print()
    print(f"Best: k1={results[0][0]:.1f}, b={results[0][1]:.2f}, MRR={results[0][2]:.4f}")
    print(f"Default: k1=1.2, b=0.75")


if __name__ == "__main__":
    main()
