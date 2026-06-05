"""API routes for AIOps RAG — ES + Neo4j + rerank."""

import json
import os
from app.models.query import (
    IndexRequest,
    QueryRequest,
    QueryResponse,
    SourceItem,
    TopologyResponse,
    HealthResponse,
)

from fastapi import APIRouter, HTTPException
from fastapi import Query as FastAPIQuery
import httpx

from app.retrievers.es_retriever import get_es_client, search_fulltext, expand_to_parents
from app.retrievers.vector_retriever import search_vector
from app.retrievers.graph_retriever import (
    get_driver, get_service_topology, get_host_impact, get_full_path,
    detect_circular_deps, get_service_docs, get_doc_services, get_doc_hosts, get_host_docs,
    get_service_cluster, get_host_cluster, check_sync_health, update_node,
)
from app.reranker.reranker import merge_and_rerank
from app.router.query_rewriter import rewrite_and_extract
from app.config import settings

router = APIRouter()

LLM_SYSTEM_PROMPT = """你是 AIOps 知识库助手。根据检索到的文档和拓扑数据回答运维问题。

数据来源说明：
- [es] = ES 全文检索（SOP/技术文档/故障报告）
- [neo4j] = Neo4j 图拓扑数据（服务→主机部署关系、服务间调用链、端口信息）
- [vector] = 向量语义检索

来源可信度标记：
- ★★★ = 高相关（评分≥0.7），可直接引用
- ★★ = 中相关（评分0.4-0.7），谨慎引用
- ★ = 低相关（评分<0.4），仅供参考

来源路径标记：
- direct = 直接检索命中
- topology_expand = 通过服务拓扑关系扩展发现
- cluster_expand = 通过集群拓扑关系扩展发现

要求：
- 综合 ES 文档内容和 Neo4j 拓扑数据回答
- **每条事实必须标注来源标记**：在陈述末尾用 [文档N] 或 [拓扑N] 标记引用
- **优先引用 ★★★ 标记的来源**，★ 标记的来源仅作补充
- **回答末尾必须给出置信度评估**：
  ```
  ---
  🔴🟡🟢 置信度: 低/中/高
  理由: （一句话说明数据覆盖度、来源质量）
  数据完整度: X/5（检索到的文档对问题的覆盖程度）
  ```
- 如果知识库有相关文档，基于文档内容回答
- 如果文档中无相关信息，明确说"当前知识库无法找到相关信息"
- 回答简洁专业，适合运维人员快速阅读
"""


def _llm_key() -> str | None:
    return settings.llm_api_key or None


async def _call_llm(messages: list[dict]) -> str:
    key = _llm_key()
    if not key:
        return ""

    import asyncio
    def _sync_call():
        resp = httpx.Client(timeout=45, http2=False).post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": settings.llm_model, "messages": messages, "temperature": 0.1, "max_tokens": 1024},
        )
        return resp.json()
    try:
        data = await asyncio.to_thread(_sync_call)
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        if total_tokens:
            try:
                from app.monitor import inc_metric
                inc_metric("llm_tokens_total", total_tokens)
            except Exception:
                pass
        return content
    except Exception as e:
        print(f"LLM call failed: {e}")
        return ""


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Multi-engine retrieval with graph enrichment, cluster awareness, degraded handling."""

    # Phase 1: Query analysis (single LLM call)
    rewritten, query_types, entities = await rewrite_and_extract(req.query)
    if rewritten != req.query:
        print(f"Query analysis: '{req.query}' -> '{rewritten}' types={query_types} entities={entities}")

    host_ip = entities.get("host_ip", "")

    _need_chain = "architecture" in query_types
    _need_topology = _need_chain or "topology" in query_types or "incident" in query_types
    # Force topology if host IP was extracted (cluster expansion)
    if host_ip:
        _need_topology = True

    es = get_es_client()
    driver = get_driver()

    all_results = []
    degraded = False
    missing_components = []

    # Phase 2: ES multi-path retrieval
    es_results = search_fulltext(es, rewritten, req.top_k)
    all_results.append(es_results)

    vec_results = await search_vector(es, rewritten, req.top_k)
    all_results.append(vec_results)

    # Tag direct hits and collect service_ids
    es_service_ids = set()
    for r in es_results:
        r["source_path"] = "direct"
        sids = r.get("service_ids", [])
        for sid in sids:
            if sid:
                es_service_ids.add(sid)
    for r in vec_results:
        r["source_path"] = "direct"

    # Phase 3: Neo4j topology enrichment
    if _need_topology and es_service_ids:
        try:
            with driver.session() as session:
                for svc_id in list(es_service_ids)[:5]:
                    # Cluster-aware expansion — prefer IP-based lookup
                    cluster_data = None
                    if host_ip:
                        cluster_data = get_host_cluster(driver, host_ip)
                    if not cluster_data or not cluster_data.get("cluster"):
                        cluster_data = get_service_cluster(driver, svc_id)

                    if cluster_data and cluster_data.get("cluster"):
                        cluster = cluster_data["cluster"]
                        all_results.append([{
                            "title": f"集群: {cluster['name']}",
                            "content": json.dumps(cluster, ensure_ascii=False),
                            "score": 1.0,
                            "engine": "neo4j",
                            "source_path": "cluster_expand",
                        }])

                    # Service topology
                    topo = get_service_topology(driver, svc_id)
                    if "error" not in topo:
                        all_results.append([{
                            "title": f"拓扑: {topo['service_name']}",
                            "content": json.dumps(topo, ensure_ascii=False),
                            "score": 1.0,
                            "engine": "neo4j",
                            "source_path": "topology_expand",
                        }])

                    # Topology→docs secondary search
                    doc_refs = get_service_docs(driver, svc_id, limit=50)
                    if doc_refs:
                        doc_ids = [d["doc_id"] for d in doc_refs][:50]
                        secondary = search_fulltext(
                            es, rewritten,
                            top_k=3,
                            doc_ids_filter=doc_ids,
                        )
                        for sr in secondary:
                            sr["engine"] = "es"
                            sr["source_path"] = "topology_expand"
                            all_results.append([sr])

                    if _need_chain:
                        chain = get_full_path(driver, svc_id, depth=4)
                        if chain:
                            all_results.append([{
                                "title": f"依赖链: {topo.get('service_name', svc_id)}",
                                "content": json.dumps(chain, ensure_ascii=False),
                                "score": 0.9,
                                "engine": "neo4j",
                                "source_path": "topology_expand",
                            }])
        except Exception as e:
            print(f"Neo4j enrichment failed: {e}")
            degraded = True
            missing_components.append("neo4j")
            try:
                from app.monitor import inc_metric
                inc_metric("degraded_queries_total")
            except Exception:
                pass

    # Phase 4: Merge, Rerank, Synthesize
    if not all_results or all(v == [] for v in all_results):
        return QueryResponse(
            answer="当前知识库无法找到相关信息。",
            sources=[],
            gap_warning="知识库对这个问题覆盖不足，建议补充相关 SOP 或技术文档。",
        )

    ranked = await merge_and_rerank(req.query, *all_results, top_k=req.top_k)
    ranked = expand_to_parents(es, ranked)

    # Confidence scoring
    for r in ranked:
        score = r.get("score", 0)
        if score >= 0.7:
            r["confidence"] = "★★★"
        elif score >= 0.4:
            r["confidence"] = "★★"
        else:
            r["confidence"] = "★"
        r.setdefault("source_path", "direct")

    # Build context
    context_parts = []
    sources = []
    for i, r in enumerate(ranked):
        sp = r.get("source_path", "direct")
        sp_note = ""
        if sp == "topology_expand":
            sp_note = " [拓扑扩展]"
        elif sp == "cluster_expand":
            sp_note = " [集群扩展]"
        context_parts.append(
            f"[文档{i+1}] ({r.get('engine')}, {r.get('confidence', '★')}{sp_note})\n{r.get('content', '')}"
        )
        sources.append(SourceItem(
            title=r.get("title", ""),
            score=r.get("score"),
            engine=r["engine"],
            snippet=r.get("snippet", r.get("content", "")[:100]),
            confidence=r.get("confidence"),
            source_path=r.get("source_path"),
        ))

    context = "\n\n---\n\n".join(context_parts)

    llm_answer = await _call_llm([
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户问题：{req.query}\n\n检索到的文档：\n{context}"},
    ])

    answer = llm_answer or f"找到 {len(ranked)} 条相关信息。\n\n上下文：\n{context}"

    # Gap detection — only when LLM genuinely can't answer
    gap_warning = None
    if (not llm_answer) or "当前知识库无法找到" in llm_answer:
        gap_warning = "知识库对这个问题覆盖不足，建议补充相关 SOP 或技术文档。"

    if degraded:
        answer += "\n\n[系统提示: 图拓扑服务当前不可用，以下分析仅基于文本检索，结果可能不完整。]"

    return QueryResponse(
        answer=answer,
        sources=sources,
        degraded=degraded,
        missing_components=missing_components,
        gap_warning=gap_warning,
    )


@router.get("/topology", response_model=TopologyResponse)
async def topology(service_id: str = FastAPIQuery(...)):
    driver = get_driver()
    try:
        topo = get_service_topology(driver, service_id)
        if "error" in topo:
            raise HTTPException(status_code=404, detail=topo["error"])
        return TopologyResponse(**topo)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/impact")
async def impact(host_id: str = FastAPIQuery(...)):
    driver = get_driver()
    try:
        result = get_host_impact(driver, host_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/graph/path")
async def graph_path(service_id: str = FastAPIQuery(...), depth: int = FastAPIQuery(5)):
    driver = get_driver()
    try:
        path = get_full_path(driver, service_id, depth)
        if not path:
            raise HTTPException(status_code=404, detail="no path found")
        return {"service_id": service_id, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/graph/circular")
async def graph_circular():
    driver = get_driver()
    try:
        circles = detect_circular_deps(driver)
        return {"circular_dependencies": circles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index")
async def index_doc(req: IndexRequest):
    from app.indexer.doc_indexer import parse_markdown, index_chunk
    from app.retrievers.es_retriever import get_es_client

    chunks = parse_markdown(req.file_path)
    if not chunks:
        raise HTTPException(status_code=400, detail="Failed to parse markdown")

    es = get_es_client()
    doc_ids = []
    for chunk in chunks:
        es_id = await index_chunk(es, chunk)
        if es_id:
            doc_ids.append(es_id)

    if not doc_ids:
        raise HTTPException(status_code=500, detail="Failed to index document")

    return {"doc_id": chunks[0].get("doc_id"), "file_path": req.file_path, "chunks": len(doc_ids)}


@router.post("/index-all")
async def index_all(req: dict = {}):
    """Incremental index all markdown files in /app/wiki/. body: {"full": true|false}."""
    import subprocess
    import sys
    full = req.get("full", False) if isinstance(req, dict) else False
    cmd = [sys.executable, "/app/scripts/index-docs.py"]
    if full:
        cmd.append("--full")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=r.stderr.strip())
    return {"status": "ok", "output": r.stdout.strip().split("\n")[-1]}


@router.post("/index-file")
async def index_file(req: IndexRequest):
    """Index a single markdown file into ES."""
    full_path = os.path.expanduser(req.file_path)
    if not full_path.startswith("/"):
        full_path = f"/app/wiki/{req.file_path}"
    from app.indexer.doc_indexer import index_single_file
    es = get_es_client()
    s, f = await index_single_file(es, full_path)
    if f > 0:
        raise HTTPException(status_code=500, detail=f"Index failed: {f} errors")
    return {"file_path": full_path, "chunks": s}


@router.post("/reload-topology")
async def reload_topology():
    """Reload call-graph.yml into Neo4j."""
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "/app/scripts/load-topology.py", "/app/wiki/topology/call-graph.yml"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=r.stderr.strip())
    return {"status": "ok", "output": r.stdout.strip()}


@router.get("/service/{service_id}/docs")
async def service_docs(service_id: str):
    driver = get_driver()
    try:
        doc_refs = get_service_docs(driver, service_id)
        return {"service_id": service_id, "docs": doc_refs, "count": len(doc_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/document/{doc_id}/services")
async def doc_services(doc_id: str):
    driver = get_driver()
    try:
        svc_refs = get_doc_services(driver, doc_id)
        return {"doc_id": doc_id, "services": svc_refs, "count": len(svc_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/document/{doc_id}/hosts")
async def doc_hosts(doc_id: str):
    driver = get_driver()
    try:
        host_refs = get_doc_hosts(driver, doc_id)
        return {"doc_id": doc_id, "hosts": host_refs, "count": len(host_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/host/{host_id}/docs")
async def host_docs(host_id: str):
    driver = get_driver()
    try:
        doc_refs = get_host_docs(driver, host_id)
        return {"host_id": host_id, "docs": doc_refs, "count": len(doc_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/node/{label}/{node_id}")
async def node_update(label: str, node_id: str, props: dict):
    """Update any node's properties. label ∈ {Service, Host, Port, Document, Cluster}.
    Port nodes identified by port number (e.g. /node/Port/9200)."""
    driver = get_driver()
    allowed = {"Service", "Host", "Port", "Document", "Cluster"}
    if label not in allowed:
        raise HTTPException(status_code=400, detail=f"Unknown label '{label}'. Allowed: {allowed}")
    # Key field varies per label
    key_field = {"Port": "number", "Cluster": "service_id"}.get(label, "id")
    try:
        # Convert Port node_id to int
        if label == "Port":
            node_id = int(node_id)
        result = update_node(driver, label, key_field, node_id, props)
        if result["status"] == "error":
            code = 404 if "not found" in str(result.get("detail", "")).lower() else 400
            raise HTTPException(status_code=code, detail=result["detail"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cluster/{service_id}")
async def cluster(service_id: str):
    driver = get_driver()
    try:
        cluster_data = get_service_cluster(driver, service_id)
        return cluster_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health/sync")
async def health_sync():
    driver = get_driver()
    try:
        result = check_sync_health(driver, settings.es_url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=HealthResponse)
async def health():
    es_status = "unknown"
    try:
        es = get_es_client()
        es_status = "ok" if es.ping() else "error"
    except Exception:
        es_status = "error"

    neo4j_status = "unknown"
    try:
        driver = get_driver()
        with driver.session() as session:
            session.run("RETURN 1")
            neo4j_status = "ok"
    except Exception as e:
        print(f"Health Neo4j check failed: {e}")
        neo4j_status = "error"

    overall = "ok" if es_status == "ok" and neo4j_status == "ok" else "degraded"

    sync_status = None
    if es_status == "ok" and neo4j_status == "ok":
        try:
            sync_result = check_sync_health(driver, settings.es_url, timeout=5)
            sync_status = {
                "status": "ok" if not sync_result.get("orphan_docs") and not sync_result.get("dangling_doc_refs") else "issues_found",
                "orphan_docs": len(sync_result.get("orphan_docs", [])),
                "dangling_service_refs": len(sync_result.get("dangling_doc_refs", [])),
                "missing_doc_edges": len(sync_result.get("missing_doc_edges", [])),
                "cluster_sync_ok": len(sync_result.get("cluster_issues", [])) == 0,
                "total_es_docs": sync_result.get("stats", {}).get("es_docs", 0),
                "total_neo4j_docs": sync_result.get("stats", {}).get("neo4j_docs", 0),
                "partial": sync_result.get("partial", False),
            }
        except Exception as e:
            print(f"Sync health check failed: {e}")

    return HealthResponse(status=overall, es=es_status, neo4j=neo4j_status, sync=sync_status)
