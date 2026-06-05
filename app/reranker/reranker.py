"""Rerank via SiliconFlow API + RRF fusion. Ref: doc/混合检索RAG实战."""

import httpx

from app.config import settings

RERANK_MODEL = "Qwen/Qwen3-Reranker-8B"
RRF_K = 60  # RRF constant


def _api_key() -> str | None:
    return settings.rerank_api_key or settings.llm_api_key or None


def reciprocal_rank_fusion(result_lists: list[list[dict]], top_k: int = 20) -> list[dict]:
    """RRF: fuse multi-engine results by rank position, not score.
    Ref: doc/混合检索RAG实战 — score(d) = sum(1 / (k + rank_i(d)))"""
    seen = {}  # key -> {item, rrf_score}
    for rank_list in result_lists:
        for rank, item in enumerate(rank_list, start=1):
            key = item.get("service_id", "") + item.get("doc_type", "") + item.get("title", "")
            if key not in seen:
                seen[key] = {"item": item, "rrf_score": 0.0}
            seen[key]["rrf_score"] += 1.0 / (RRF_K + rank)

    sorted_items = sorted(seen.values(), key=lambda x: x["rrf_score"], reverse=True)
    return [entry["item"] for entry in sorted_items][:top_k]


async def rerank(query: str, results: list[dict], top_k: int = 5) -> list[dict]:
    """Rerank via SiliconFlow API, fallback score sort."""
    if not results:
        return []

    key = _api_key()
    if not key:
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:top_k]

    base = settings.rerank_base_url
    documents = [r.get("content", "") or r.get("title", "") for r in results]

    try:
        import asyncio
        def _sync():
            resp = httpx.Client(timeout=30, http2=False).post(
                f"{base}/rerank",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": RERANK_MODEL, "query": query, "documents": documents, "top_n": top_k},
            )
            return resp.json()
        data = await asyncio.to_thread(_sync)

        ranked = []
        for item in data.get("results", []):
            idx = item["index"]
            results[idx]["score"] = item["relevance_score"]
            ranked.append(results[idx])
        return ranked
    except Exception as e:
        print(f"Rerank error: {e}, fallback score sort")
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:top_k]


async def merge_and_rerank(query: str, *result_lists: list[dict], top_k: int = 5) -> list[dict]:
    """RRF fusion → rerank. Ref: doc/混合检索RAG实战."""
    fused = reciprocal_rank_fusion(result_lists)
    return await rerank(query, fused, top_k)
