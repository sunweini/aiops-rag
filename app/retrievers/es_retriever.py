from elasticsearch import Elasticsearch
from app.config import settings

INDEX_NAME = "knowledge_base"

_es_client: Elasticsearch | None = None

def get_es_client() -> Elasticsearch:
    global _es_client
    if _es_client is None:
        auth = (settings.es_user, settings.es_password) if settings.es_password else None
        _es_client = Elasticsearch(settings.es_url, basic_auth=auth)
    return _es_client

def close_es():
    global _es_client
    if _es_client:
        _es_client.close()
        _es_client = None

def init_index(es: Elasticsearch):
    """Create index with IK+BM25 config. Ref: doc/ES全文检索"""
    if es.indices.exists(index=INDEX_NAME):
        return

    es.indices.create(
        index=INDEX_NAME,
        body={
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "similarity": {
                        "custom_bm25": {
                            "type": "BM25",
                            "k1": 1.2,
                            "b": 0.75,
                        }
                    },
                },
                "analysis": {
                    "analyzer": {
                        "ik_index_analyzer": {
                            "type": "custom",
                            "tokenizer": "ik_max_word",
                            "filter": ["lowercase"],
                        },
                        "ik_search_analyzer": {
                            "type": "custom",
                            "tokenizer": "ik_smart",
                            "filter": ["lowercase"],
                        },
                    }
                },
            },
            "mappings": {
                "properties": {
                    "title": {
                        "type": "text",
                        "analyzer": "ik_index_analyzer",
                        "search_analyzer": "ik_search_analyzer",
                    },
                    "content": {
                        "type": "text",
                        "analyzer": "ik_index_analyzer",
                        "search_analyzer": "ik_search_analyzer",
                    },
                    "chunk_index": {"type": "keyword"},
                    "chunk_total": {"type": "integer"},
                    "doc_id": {"type": "keyword"},
                    "doc_type": {"type": "keyword"},
                    "service_ids": {"type": "keyword"},
                    "service_name": {"type": "keyword"},
                    "tags": {"type": "keyword"},
                    "chunk_type": {"type": "keyword"},
                    "parent_id": {"type": "keyword"},
                    "host_ids": {"type": "keyword"},
                    "updated_at": {"type": "date"},
                    "content_vector": {
                        "type": "dense_vector",
                        "dims": 4096,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            },
        },
    )


def search_fulltext(es: Elasticsearch, query: str, top_k: int = 5, doc_type: str | None = None, doc_ids_filter: list[str] | None = None, exclude_parents: bool = True) -> list[dict]:
    """Full-text search with IK+BM25 using ik_smart analyzer for queries.
    Optional doc_ids_filter for topology→doc secondary retrieval.
    exclude_parents=True: skip parent chunks from primary search (only children match)."""
    should_clause = [
        {
            "multi_match": {
                "query": query,
                "fields": ["title^2", "content"],
                "type": "best_fields",
                "operator": "or",
                "analyzer": "ik_search_analyzer",
                "minimum_should_match": "30%",
                "fuzziness": "AUTO",
            }
        },
        {"term": {"service_ids": {"value": query.lower(), "boost": 3.0}}},
        {"term": {"service_name": {"value": query.lower(), "boost": 2.0}}},
    ]
    must_clause = [{"bool": {"should": should_clause, "minimum_should_match": 1}}]
    if doc_type:
        must_clause.append({"term": {"doc_type": doc_type}})
    if doc_ids_filter:
        must_clause.append({"terms": {"doc_id": doc_ids_filter}})
    if exclude_parents:
        must_clause.append({"bool": {"must_not": [{"term": {"chunk_type": "parent"}}]}})

    try:
        resp = es.search(
            index=INDEX_NAME,
            body={
                "size": top_k,
                "query": {"bool": {"must": must_clause}},
                "highlight": {
                    "fields": {
                        "content": {"fragment_size": 200, "number_of_fragments": 3},
                        "title": {"fragment_size": 100, "number_of_fragments": 1},
                    }
                },
            },
        )
    except Exception as e:
        print(f"ES search error: {e}")
        return []

    results = []
    for hit in resp.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        highlight = hit.get("highlight", {})
        snippet = "...".join(
            highlight.get("content", []) + highlight.get("title", [])
        )
        results.append({
            "title": src.get("title", ""),
            "content": src.get("content", ""),
            "score": hit["_score"],
            "snippet": snippet,
            "doc_type": src.get("doc_type", ""),
            "chunk_type": src.get("chunk_type", "flat"),
            "parent_id": src.get("parent_id", ""),
            "service_ids": src.get("service_ids", []),
            "engine": "es",
        })
    return results


def expand_to_parents(es: Elasticsearch, results: list[dict]) -> list[dict]:
    """For child chunk results, fetch parent sections to replace content.
    Used for SOP parent-child chunking: search hits child, returns parent section.
    Non-SOP results pass through unchanged."""
    parent_ids_to_fetch = []
    child_indices = []
    for i, r in enumerate(results):
        pid = r.get("parent_id", "")
        ctype = r.get("chunk_type", "flat")
        if pid and ctype == "child":
            parent_ids_to_fetch.append(pid)
            child_indices.append(i)

    if not parent_ids_to_fetch:
        return results

    # Batch fetch parents by ES _id (parent_id is the parent's ES _id)
    try:
        resp = es.search(index=INDEX_NAME, body={
            "size": len(parent_ids_to_fetch),
            "query": {"terms": {"_id": parent_ids_to_fetch}},
            "_source": ["content", "title", "doc_type"],
        })
        parents = {}
        for hit in resp["hits"]["hits"]:
            parents[hit["_id"]] = hit["_source"]

        for idx, pid in zip(child_indices, parent_ids_to_fetch):
            parent = parents.get(pid)
            if parent:
                results[idx]["content"] = parent.get("content", results[idx]["content"])
                results[idx]["parent_expanded"] = True
                results[idx]["original_snippet"] = results[idx].get("snippet", "")
    except Exception as e:
        print(f"Parent expansion error: {e}")

    return results


def get_docs_by_ids(es: Elasticsearch, doc_ids: list[str], top_k: int = 50) -> list[dict]:
    """Fetch documents by doc_id list. For topology→doc secondary retrieval."""
    if not doc_ids:
        return []

    doc_ids = doc_ids[:50]
    if len(doc_ids) > 50:
        print(f"get_docs_by_ids: truncating to 50")

    try:
        resp = es.search(
            index=INDEX_NAME,
            body={
                "size": top_k,
                "query": {"bool": {"must": [{"terms": {"doc_id": doc_ids}}]}},
            },
        )
        results = []
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            results.append({
                "title": src.get("title", ""),
                "content": src.get("content", ""),
                "score": hit["_score"],
                "doc_type": src.get("doc_type", ""),
                "chunk_type": src.get("chunk_type", "flat"),
                "parent_id": src.get("parent_id", ""),
                "service_ids": src.get("service_ids", []),
                "engine": "es",
            })
        return results
    except Exception as e:
        print(f"get_docs_by_ids error: {e}")
        return []
