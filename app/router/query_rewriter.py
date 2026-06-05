"""Query rewrite + entity extraction + intent classification via single LLM call."""

import json
import re

import httpx

from app.config import settings

MERGED_PROMPT = """你是运维查询分析专家。一次调用完成三件事：改写查询、提取实体、分类意图。

输出 JSON 格式：
{"query": "改写后的检索短文本", "type": ["分类"], "entities": {"host_ip": "IP或空", "service": "服务名或空", "port": "端口号或空", "symptom": "症状关键词或空"}}

分类规则：
- sop: 操作步骤（如何重启、怎么扩容、步骤流程）
- topology: 单服务拓扑（部署在哪台机器、端口是什么）
- architecture: 架构依赖链（依赖哪些服务、调用关系、影响链路）
- incident: 故障排查（502、延时高、连不上、报错、异常）

实体提取规则：
- host_ip: 用户提到的 IP 地址（如 10.33.16.42），没有则空字符串
- service: 用户提到的服务名（如 nginx、redis、order-service），没有则空字符串
- port: 用户提到的端口号（如 502、443），没有则空字符串
- symptom: 故障症状关键词（如 502、超时、连不上、延时高），没有则空字符串

示例：
用户："nginx 最近老是 502 怎么回事"
→ {"query": "nginx 502 Bad Gateway 排查", "type": ["incident"], "entities": {"host_ip": "", "service": "nginx", "port": "", "symptom": "502"}}

用户："10.33.16.42 上 nginx 502 排查"
→ {"query": "nginx 502 故障排查", "type": ["incident", "topology"], "entities": {"host_ip": "10.33.16.42", "service": "nginx", "port": "", "symptom": "502"}}

用户："帮我查下 order-service 部署在哪台机器"
→ {"query": "order-service 部署 主机", "type": ["topology"], "entities": {"host_ip": "", "service": "order-service", "port": "", "symptom": ""}}

用户："订单服务依赖哪些服务 完整调用链"
→ {"query": "order-service 依赖 调用链", "type": ["architecture"], "entities": {"host_ip": "", "service": "order-service", "port": "", "symptom": ""}}
"""

VALID_TYPES = {"sop", "topology", "architecture", "incident"}

IP_PATTERN = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

_ENTITY_CACHE = None
_ENTITY_CACHE_TTL = 0


def _api_key() -> str | None:
    return settings.llm_api_key or None


def _load_entity_cache() -> dict:
    """Lazy-load known service names and host IDs from ES aggregation."""
    try:
        from app.retrievers.es_retriever import get_es_client, INDEX_NAME
        es = get_es_client()
        resp = es.search(index=INDEX_NAME, body={
            "size": 0,
            "aggs": {
                "service_names": {"terms": {"field": "service_name", "size": 50}},
                "host_ids": {"terms": {"field": "host_ids", "size": 50}},
            },
        })
        return {
            "service_names": [b["key"] for b in resp["aggregations"]["service_names"]["buckets"]],
            "host_ids": [b["key"] for b in resp["aggregations"]["host_ids"]["buckets"]],
        }
    except Exception as e:
        print(f"Entity cache load error: {e}")
        return {"service_names": [], "host_ids": []}


def _regex_extract_entities(query: str) -> dict:
    """Fallback regex-based entity extraction. Fast, deterministic."""
    entities = {"host_ip": "", "service": "", "port": "", "symptom": ""}
    m = IP_PATTERN.search(query)
    if m:
        entities["host_ip"] = m.group(1)

    global _ENTITY_CACHE, _ENTITY_CACHE_TTL
    import time
    if _ENTITY_CACHE is None or int(time.time()) - _ENTITY_CACHE_TTL > 300:
        _ENTITY_CACHE = _load_entity_cache()
        _ENTITY_CACHE_TTL = int(time.time())

    query_lower = query.lower()
    if _ENTITY_CACHE:
        for svc_name in _ENTITY_CACHE.get("service_names", []):
            if svc_name.lower() in query_lower and len(svc_name) > 2:
                entities["service"] = svc_name
                break

    return entities


async def rewrite_and_extract(query: str) -> tuple[str, list[str], dict]:
    """One LLM call: rewrite + classify + extract entities.
    Returns (rewritten_query, types_list, entities_dict).
    Falls back to regex entity extraction on LLM error."""
    key = _api_key()
    if not key:
        return query, ["sop"], _regex_extract_entities(query)

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": MERGED_PROMPT},
            {"role": "user", "content": query},
        ],
        "temperature": 0,
        "max_tokens": 200,
    }

    try:
        import asyncio
        def _sync():
            resp = httpx.Client(timeout=30, http2=False).post(
                f"{settings.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            return resp.json()
        data = await asyncio.to_thread(_sync)
        text = data["choices"][0]["message"]["content"].strip()

        if "```" in text:
            m = re.search(r'`{3,}(?:json)?\s*(\{.*?\})\s*`{3,}', text, re.DOTALL)
            if m:
                text = m.group(1)
        result = json.loads(text)

        r_query = result.get("query", "").strip()
        r_types = result.get("type", [])
        if isinstance(r_types, str):
            r_types = [r_types]
        r_types = [t for t in r_types if t in VALID_TYPES]

        llm_entities = result.get("entities", {})
        if not isinstance(llm_entities, dict):
            llm_entities = {}

        # Merge LLM entities with regex (union — LLM may miss IP)
        regex_entities = _regex_extract_entities(query)
        merged_entities = {}
        for key in ("host_ip", "service", "port", "symptom"):
            llm_val = llm_entities.get(key, "")
            regex_val = regex_entities.get(key, "")
            merged_entities[key] = llm_val if llm_val else regex_val

        if not r_query or len(r_query) < 2 or not r_types:
            return query, ["sop"], merged_entities

        return r_query, r_types, merged_entities

    except Exception as e:
        print(f"Rewrite+extract LLM error (using regex fallback): {e}")
        try:
            from app.monitor import inc_metric
            inc_metric("entity_extract_fallback_total")
        except Exception:
            pass
        return query, ["sop"], _regex_extract_entities(query)


async def rewrite_query(query: str) -> tuple[str, list[str]]:
    rewritten, types, _ = await rewrite_and_extract(query)
    return rewritten, types
