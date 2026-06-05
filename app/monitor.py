"""Request monitoring middleware. Track latency, count, errors per endpoint."""

import time
from collections import defaultdict

import json

_gauge = {
    "sync_errors_total": 0,
    "orphan_docs_total": 0,
    "degraded_queries_total": 0,
    "entity_extract_fallback_total": 0,
    "llm_tokens_total": 0,
}

_stats = {
    "requests": defaultdict(int),      # endpoint -> count
    "latency": defaultdict(list),      # endpoint -> [ms, ...]
    "errors": defaultdict(int),        # endpoint -> error count
    "sources_per_query": [],           # source count for /query
    "start_time": time.time(),
}


async def monitor_middleware(request, call_next):
    start = time.time()
    path = request.url.path

    try:
        response = await call_next(request)
        status = response.status_code
        ms = (time.time() - start) * 1000

        _stats["requests"][path] += 1
        _stats["latency"][path].append(ms)
        # Keep only last 1000 for memory
        if len(_stats["latency"][path]) > 1000:
            _stats["latency"][path] = _stats["latency"][path][-1000:]

        if status >= 400:
            _stats["errors"][path] += 1

        return response
    except Exception as e:
        ms = (time.time() - start) * 1000
        _stats["errors"][path] += 1
        raise


def get_metrics() -> dict:
    """Return current metrics snapshot."""
    result = {
        "uptime_seconds": int(time.time() - _stats["start_time"]),
        "endpoints": {},
    }
    for path in sorted(_stats["requests"]):
        latencies = _stats["latency"].get(path, [])
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        result["endpoints"][path] = {
            "total": _stats["requests"][path],
            "errors": _stats["errors"].get(path, 0),
            "avg_latency_ms": round(avg_latency, 1),
            "max_latency_ms": round(max(latencies), 1) if latencies else 0,
        }
    result["gauges"] = dict(_gauge)
    return result


def inc_metric(name: str, delta: int = 1):
    if name in _gauge:
        _gauge[name] += delta


def set_metric(name: str, value: int):
    if name in _gauge:
        _gauge[name] = value
