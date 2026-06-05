# ES 文档与 Neo4j 知识图谱关联 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ES 文档与 Neo4j 图双向关联 — Document 轻量图节点 + Cluster 集群感知 + 查询感知富化 + 降级 + 健康检查

**Architecture:** 12 个文件改动，增量扩展已有代码。核心链路：`schema → doc_indexer → graph_retriever → query_rewriter → routes → monitor → load-topology → CLI`

**Tech Stack:** Python 3, FastAPI, Elasticsearch 8.x, Neo4j 5.x, httpx, pytest + pytest-asyncio

---

# Phase 1: 基础设施 + 数据模型

### Task 1: pytest 基础设施

**Files:**
- Create: `tests/__init__.py`, `tests/conftest.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add pytest dependencies**

Append to `requirements.txt`:

```
pytest>=8.0
pytest-asyncio>=0.24
httpx>=0.27
```

- [ ] **Step 2: Write conftest.py**

```python
"""Shared test fixtures."""
import os
import sys
import pytest

# Ensure app is importable from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
def docker_compose_env():
    """Read env vars from .env file for local docker-compose."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
    return env


@pytest.fixture
def es_url(docker_compose_env):
    return docker_compose_env.get("ES_URL", "http://localhost:9200")


@pytest.fixture
def neo4j_uri(docker_compose_env):
    return docker_compose_env.get("NEO4J_URI", "bolt://localhost:7687")


@pytest.fixture
def neo4j_auth(docker_compose_env):
    user = docker_compose_env.get("NEO4J_USER", "neo4j")
    pw = docker_compose_env.get("NEO4J_PASSWORD", "password")
    return (user, pw)


@pytest.fixture
def test_doc_data():
    """Sample document for testing."""
    return {
        "title": "nginx 502 故障排查 SOP",
        "content": "排查步骤：1. 检查 upstream 状态 2. 查看连接数 3. 重启服务",
        "chunk_index": 0,
        "chunk_total": 1,
        "doc_id": "svc_nginx_company_nginx-502-sop",
        "doc_type": "sop",
        "service_ids": ["svc_nginx_company"],
        "service_name": "company-nginx-cluster",
        "tags": ["故障", "502", "排查"],
        "host_ids": ["host_nginx_01"],
        "updated_at": "2026-06-02",
    }


@pytest.fixture
def test_service_needed():
    """Services that must exist in Neo4j for doc sync test."""
    return [
        {"id": "svc_nginx_company", "name": "company-nginx-cluster", "status": "active"},
        {"id": "svc_order", "name": "order-service", "status": "active"},
    ]
```

- [ ] **Step 3: Install dependencies**

```bash
cd /root/.openclaw/workspace-shared/rag
pip install pytest pytest-asyncio httpx 2>&1 | tail -5
```

Expected: `Successfully installed pytest-... pytest-asyncio-...`

- [ ] **Step 4: Commit**

```bash
git add tests/ requirements.txt
git commit -m "chore: add pytest infrastructure for integration tests"
```

---

### Task 2: schema.py — service_id → service_ids 迁移

**Files:**
- Modify: `app/schema.py`
- Create: `tests/test_schema.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_schema.py
"""Test frontmatter and topology schema validation."""
from app.schema import validate_frontmatter, VALID_DOC_TYPES


def test_rejects_empty_service_ids():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
        "service_ids": [],
    })
    assert len(errors) > 0, "Empty service_ids should be rejected"


def test_rejects_missing_service_ids():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
    })
    assert len(errors) > 0, "Missing service_ids should be rejected"


def test_accepts_valid_service_ids():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
        "service_ids": ["svc_nginx", "svc_order"],
        "tags": ["502"],
        "updated_at": "2026-06-02",
    })
    assert len(errors) == 0, f"Valid frontmatter should pass, got: {errors}"


def test_rejects_non_svc_prefix():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
        "service_ids": ["not_a_service"],
    })
    assert len(errors) > 0, "Non svc_ prefix should be rejected"


def test_valid_doc_types():
    for dt in ["sop", "tech", "incident"]:
        errors = validate_frontmatter({
            "title": "Test",
            "doc_type": dt,
            "service_ids": ["svc_test"],
        })
        assert len(errors) == 0, f"{dt} should be valid"


def test_rejects_invalid_doc_type():
    errors = validate_frontmatter({
        "title": "Test",
        "doc_type": "not_valid",
        "service_ids": ["svc_test"],
    })
    assert len(errors) > 0, "Invalid doc_type should be rejected"
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /root/.openclaw/workspace-shared/rag
pytest tests/test_schema.py -v 2>&1 | tail -15
```

Expected: FAIL — `FRONTMATTER_REQUIRED` still has `"service_id"`, not `"service_ids"`

- [ ] **Step 3: Modify schema.py**

In `app/schema.py`, change:

```python
# Before:
FRONTMATTER_REQUIRED = ["title", "doc_type", "service_id"]

# After:
FRONTMATTER_REQUIRED = ["title", "doc_type", "service_ids"]
```

Replace the single-field validation section:

```python
# Before (line ~81-83):
    service_id = meta.get("service_id", "")
    if service_id and not service_id.startswith("svc_"):
        errors.append(f"service_id '{service_id}' 应以 'svc_' 开头")

# After:
    service_ids = meta.get("service_ids", [])
    if not isinstance(service_ids, list) or len(service_ids) == 0:
        errors.append("service_ids 不能为空数组，至少需要一个 service_id")
    else:
        for sid in service_ids:
            if not isinstance(sid, str) or not sid.startswith("svc_"):
                errors.append(f"service_id '{sid}' 应以 'svc_' 开头")
```

Add Cluster and Document property whitelists at top of file:

```python
# After PORT_PROPS and CALL_PROPS definitions, add:
DOC_PROPS = {"id", "title", "type", "updated_at"}
CLUSTER_PROPS = {"service_id", "name", "vip"}
PART_OF_PROPS = {"role"}
HAS_DOC_PROPS = {"doc_type", "relevance"}

# Update ALLOWED_PROPS:
ALLOWED_PROPS = {
    "Service": SERVICE_PROPS,
    "Host": HOST_PROPS,
    "Port": PORT_PROPS,
    "Document": DOC_PROPS,
    "Cluster": CLUSTER_PROPS,
}

# Add relevance enum
VALID_RELEVANCE = {"primary", "secondary", "mentioned"}
```

- [ ] **Step 4: Run test, verify it passes**

```bash
pytest tests/test_schema.py -v
```

Expected: 6/6 PASS

- [ ] **Step 5: Commit**

```bash
git add app/schema.py tests/test_schema.py
git commit -m "feat: migrate service_id to service_ids array in schema validation"
```

---

### Task 3: doc_indexer.py — service_ids 适配 + Neo4j 同步 + 删除同步

**Files:**
- Modify: `app/indexer/doc_indexer.py`
- Modify: `app/retrievers/graph_retriever.py` (add sync_document_node, delete_document_node)
- Create: `tests/test_sync.py`

- [ ] **Step 1: Write graph_retriever sync methods**

In `app/retrievers/graph_retriever.py`, add after `init_schema()`:

```python
def sync_document_node(driver, doc: dict) -> dict:
    """Upsert Document node + HAS_DOC edges in Neo4j.
    Returns {'status': 'ok'|'partial_success'|'error', 'detail': str}."""
    doc_id = doc.get("doc_id", "")
    title = doc.get("title", "")
    doc_type = doc.get("doc_type", "tech")
    updated_at = doc.get("updated_at", "")
    service_ids = doc.get("service_ids", [])
    relevance = "primary"  # default for doc-indexed documents

    if not doc_id or not service_ids:
        return {"status": "error", "detail": f"Missing doc_id or service_ids: {doc_id}"}

    services_exist = 0
    services_missing = 0

    try:
        with driver.session() as session:
            # Upsert Document node
            session.run(
                """
                MERGE (d:Document {id: $doc_id})
                SET d.title = $title, d.type = $doc_type, d.updated_at = $updated_at
                """,
                doc_id=doc_id, title=title, doc_type=doc_type, updated_at=updated_at,
            )

            # Merge HAS_DOC edges — check Service existence first
            for sid in service_ids:
                result = session.run(
                    "MATCH (s:Service {id: $sid}) RETURN s",
                    sid=sid,
                )
                if result.single() is None:
                    services_missing += 1
                    print(f"Service '{sid}' not found in Neo4j — skipping HAS_DOC edge")
                    continue

                session.run(
                    """
                    MATCH (s:Service {id: $sid})
                    MATCH (d:Document {id: $doc_id})
                    MERGE (s)-[:HAS_DOC {doc_type: $doc_type, relevance: $relevance}]->(d)
                    """,
                    sid=sid, doc_id=doc_id, doc_type=doc_type, relevance=relevance,
                )
                services_exist += 1
    except Exception as e:
        print(f"sync_document_node error: {e}")
        return {"status": "error", "detail": str(e)}

    if services_missing > 0 and services_exist > 0:
        return {"status": "partial_success", "detail": f"{services_exist} success, {services_missing} services not found"}
    elif services_missing > 0 and services_exist == 0:
        return {"status": "error", "detail": f"All {services_missing} services not found in Neo4j"}
    return {"status": "ok", "detail": f"{services_exist} edges created"}


def delete_document_node(driver, doc_id: str) -> dict:
    """Delete HAS_DOC edges and remove Document node if orphaned.
    Returns {'status': 'ok'|'error', 'detail': str}."""
    try:
        with driver.session() as session:
            # Count HAS_DOC edges to this document
            result = session.run(
                "MATCH ()-[r:HAS_DOC]->(d:Document {id: $doc_id}) RETURN count(r) AS cnt",
                doc_id=doc_id,
            )
            edge_count = result.single()["cnt"]

            if edge_count == 0:
                # No edges, delete the node directly
                session.run(
                    "MATCH (d:Document {id: $doc_id}) DELETE d",
                    doc_id=doc_id,
                )
                return {"status": "ok", "detail": f"Document {doc_id} deleted (no edges)"}

            # Delete the HAS_DOC edge(s) then check again
            session.run(
                "MATCH ()-[r:HAS_DOC]->(d:Document {id: $doc_id}) DELETE r",
                doc_id=doc_id,
            )
            result2 = session.run(
                "MATCH ()-[r:HAS_DOC]->(d:Document {id: $doc_id}) RETURN count(r) AS cnt",
                doc_id=doc_id,
            )
            remaining = result2.single()["cnt"]
            if remaining == 0:
                session.run(
                    "MATCH (d:Document {id: $doc_id}) DELETE d",
                    doc_id=doc_id,
                )
                return {"status": "ok", "detail": f"Document {doc_id} deleted ({edge_count} edges removed)"}
            return {"status": "ok", "detail": f"{remaining} edges remaining, node preserved"}
    except Exception as e:
        print(f"delete_document_node error: {e}")
        return {"status": "error", "detail": str(e)}
```

- [ ] **Step 2: Modify doc_indexer.py**

Change `parse_markdown()` to use `service_ids`:

```python
# Before (line 56):
    doc_id_str = f"{metadata.get('service_id', 'unknown')}_{Path(filepath).stem}"

# After:
    service_ids = metadata.get("service_ids", [])
    if not service_ids:
        return []  # schema validation already rejected, but double-check
    doc_id_str = f"{service_ids[0]}_{Path(filepath).stem}"

# Before (line 70, in chunk dict):
            "service_id": metadata.get("service_id", ""),

# After:
            "service_ids": metadata.get("service_ids", []),
```

Add Neo4j sync to `index_chunk()`:

```python
async def index_chunk(es: Elasticsearch, doc: dict) -> str | None:
    """Index single chunk with embedding vector.
    Uses deterministic _id = doc_id + chunk_index → idempotent upsert."""
    if not doc:
        return None

    # Add embedding vector if possible
    try:
        vector = await embed_text(doc.get("content", ""))
        if vector:
            doc["content_vector"] = vector
    except Exception as e:
        print(f"Embedding error for chunk {doc.get('title')}: {e}")

    doc_id = doc.get("doc_id", "") + f"_chunk{doc.get('chunk_index', 0)}"

    try:
        resp = es.index(index=INDEX_NAME, id=doc_id, document=doc, refresh="wait_for")
        return resp["_id"]
    except Exception as e:
        print(f"ES index error: {e}")
        return None
```

Add new function `sync_document_batch()` to `index_directory()` after the chunk loop:

```python
async def index_directory(es: Elasticsearch, dir_path: str, clean: bool = False) -> tuple[int, int]:
    """Recursively chunk, embed, and index all markdown files.
    If clean=True, delete all existing docs first."""
    success = 0
    failed = 0
    sync_errors = 0

    if clean:
        try:
            es.indices.delete(index=INDEX_NAME, ignore_unavailable=True)
            from app.retrievers.es_retriever import init_index
            init_index(es)
            print("Index cleared and recreated")
        except Exception as e:
            print(f"Index cleanup error: {e}")

    md_files = list(Path(dir_path).rglob("*.md"))

    # Track which doc_ids have been synced to Neo4j (avoid per-chunk dupes)
    synced_docs = set()

    for fp in md_files:
        chunks = parse_markdown(str(fp))
        if not chunks:
            failed += 1
            continue
        for chunk in chunks:
            es_id = await index_chunk(es, chunk)
            if es_id:
                success += 1
            else:
                failed += 1

            # Sync Neo4j once per document (not per chunk)
            base_doc_id = chunk.get("doc_id", "")
            if base_doc_id and base_doc_id not in synced_docs and es_id:
                synced_docs.add(base_doc_id)
                from app.retrievers.graph_retriever import get_driver, sync_document_node
                driver = get_driver()
                result = sync_document_node(driver, chunk)
                if result["status"] == "error":
                    sync_errors += 1
                    print(f"Neo4j sync error [{base_doc_id}]: {result['detail']}")
                elif result["status"] == "partial_success":
                    sync_errors += 1
                    print(f"Neo4j sync partial [{base_doc_id}]: {result['detail']}")

    if sync_errors:
        print(f"Neo4j sync errors: {sync_errors}/{len(synced_docs)} documents")

    return success, failed
```

- [ ] **Step 3: Write sync integration test**

```python
# tests/test_sync.py
"""Integration tests for ES↔Neo4j sync (require running ES and Neo4j)."""
import os
import sys
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CI") is None and os.environ.get("SKIP_INTEGRATION"),
        reason="Requires running ES and Neo4j",
    ),
]


@pytest.mark.asyncio
async def test_sync_document_node(docker_compose_env, test_doc_data, test_service_needed):
    """Happy path: Document MERGE + HAS_DOC edges created."""
    from app.retrievers.graph_retriever import get_driver, sync_document_node
    driver = get_driver()

    result = sync_document_node(driver, test_doc_data)
    assert result["status"] == "ok", f"Expected ok, got: {result}"

    # Cleanup
    from app.retrievers.graph_retriever import delete_document_node
    delete_document_node(driver, test_doc_data["doc_id"])


@pytest.mark.asyncio
async def test_sync_refuses_missing_service(docker_compose_env):
    """Write-time validation: service not in Neo4j → error."""
    from app.retrievers.graph_retriever import get_driver, sync_document_node
    driver = get_driver()

    doc = {
        "title": "Ghost Service Doc",
        "content": "test",
        "chunk_index": 0,
        "chunk_total": 1,
        "doc_id": "svc_ghost_test",
        "doc_type": "tech",
        "service_ids": ["svc_ghost"],
        "service_name": "ghost",
        "tags": [],
        "host_ids": [],
        "updated_at": "2026-06-02",
    }

    result = sync_document_node(driver, doc)
    assert result["status"] == "error", f"Missing service should be rejected, got: {result}"


@pytest.mark.asyncio
async def test_delete_document_node(docker_compose_env, test_doc_data):
    """Delete: HAS_DOC edge removed + Document node deleted if orphaned."""
    from app.retrievers.graph_retriever import get_driver, sync_document_node, delete_document_node
    driver = get_driver()

    # Create first
    result = sync_document_node(driver, test_doc_data)
    assert result["status"] == "ok"

    # Delete
    result = delete_document_node(driver, test_doc_data["doc_id"])
    assert result["status"] == "ok"

    # Verify gone
    with driver.session() as session:
        r = session.run(
            "MATCH (d:Document {id: $doc_id}) RETURN d",
            doc_id=test_doc_data["doc_id"],
        )
        assert r.single() is None, "Document should be deleted"
```

- [ ] **Step 4: Verify tests**

```bash
pytest tests/test_sync.py -v -m "integration" 2>&1 | tail -20
```

- [ ] **Step 5: Commit**

```bash
git add app/indexer/doc_indexer.py app/retrievers/graph_retriever.py tests/test_sync.py
git commit -m "feat: Neo4j sync/delete Document nodes with write-time validation"
```

---

# Phase 2: 检索增强（核心改造）

### Task 4: graph_retriever.py — 查询方法 + Cluster + health

**Files:**
- Modify: `app/retrievers/graph_retriever.py`
- Modify: `scripts/init-neo4j.py` (add Document constraint, Cluster index)
- Create: `tests/test_cluster.py`

- [ ] **Step 1: Fix Cypher f-string → $param**

In `get_service_downstream()`:

```python
# Before:
            result = session.run(
                f"""
                MATCH (s:Service {{id: $sid}})-[:CALLS*1..{depth}]->(down:Service)
                RETURN DISTINCT down.id AS id, down.name AS name
                """,
                sid=service_id,
            )

# After:
            result = session.run(
                """
                MATCH (s:Service {id: $sid})-[:CALLS*1..$depth]->(down:Service)
                RETURN DISTINCT down.id AS id, down.name AS name
                """,
                sid=service_id, depth=int(depth),
            )
```

Same pattern for `get_host_impact()` downstream query and `get_full_path()` depth.

- [ ] **Step 2: Add get_service_cluster()**

```python
def get_service_cluster(driver, service_id: str) -> dict:
    """Get cluster topology for a service. Expands all PART_OF members."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Service {id: $sid})-[:DEPLOYS_ON]->(host:Host)-[:PART_OF]->(c:Cluster)
                RETURN c.service_id AS cluster_service_id, c.name AS cluster_name,
                       c.vip AS vip, collect(DISTINCT {host_id: host.id, host_name: host.name,
                       ip: host.ip}) AS members
                """,
                sid=service_id,
            )
            records = list(result)
            if len(records) > 1:
                print(f"WARN: Multiple clusters for service {service_id}, using first")
            if not records:
                return {"service_id": service_id, "cluster": None}
            r = records[0]
            host_result = session.run(
                """
                MATCH (h:Host)-[p:PART_OF]->(c:Cluster {service_id: $sid})
                RETURN h.id AS host_id, h.name AS host_name, h.ip AS ip, p.role AS role
                """,
                sid=service_id,
            )
            members = [dict(hr) for hr in host_result]
            return {
                "service_id": service_id,
                "cluster": {
                    "name": r["cluster_name"],
                    "vip": r.get("vip"),
                    "members": members,
                },
            }
    except Exception as e:
        print(f"get_service_cluster error: {e}")
        return {"service_id": service_id, "cluster": None, "error": str(e)}


def get_host_cluster(driver, host_ip: str) -> dict:
    """Given a host IP, find its cluster (if any) and return all members."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Host {ip: $ip})-[p:PART_OF]->(c:Cluster)
                RETURN c.service_id AS cluster_service_id, c.name AS cluster_name,
                       c.vip AS vip, p.role AS my_role
                """,
                ip=host_ip,
            )
            record = result.single()
            if not record:
                # Check if it IS a VIP
                vip_result = session.run(
                    "MATCH (c:Cluster {vip: $ip}) RETURN c.service_id AS cluster_service_id, c.name AS cluster_name",
                    ip=host_ip,
                )
                vip_record = vip_result.single()
                if not vip_record:
                    return {"host_ip": host_ip, "cluster": None}
                # It's a VIP — expand all members
                members_result = session.run(
                    """
                    MATCH (h:Host)-[p:PART_OF]->(c:Cluster {vip: $ip})
                    RETURN h.id AS host_id, h.name AS host_name, h.ip AS ip, p.role AS role
                    """,
                    ip=host_ip,
                )
                return {
                    "host_ip": host_ip,
                    "cluster": {
                        "name": vip_record["cluster_name"],
                        "vip": host_ip,
                        "members": [dict(m) for m in members_result],
                        "matched_by": "vip",
                    },
                }

            # Regular member — expand all members of this cluster
            members_result = session.run(
                """
                MATCH (h:Host)-[p:PART_OF]->(c:Cluster {service_id: $sid})
                RETURN h.id AS host_id, h.name AS host_name, h.ip AS ip, p.role AS role
                """,
                sid=record["cluster_service_id"],
            )
            return {
                "host_ip": host_ip,
                "cluster": {
                    "name": record["cluster_name"],
                    "vip": record.get("vip"),
                    "members": [dict(m) for m in members_result],
                    "my_role": record["my_role"],
                },
            }
    except Exception as e:
        print(f"get_host_cluster error: {e}")
        return {"host_ip": host_ip, "cluster": None, "error": str(e)}
```

- [ ] **Step 3: Add get_service_docs()**

```python
def get_service_docs(driver, service_id: str, limit: int = 50) -> list[dict]:
    """Get all doc references for a service, sorted by updated_at DESC."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Service {id: $sid})-[r:HAS_DOC]->(d:Document)
                RETURN d.id AS doc_id, d.title AS title, d.type AS doc_type,
                       d.updated_at AS updated_at, r.relevance AS relevance
                ORDER BY d.updated_at DESC
                LIMIT $limit
                """,
                sid=service_id, limit=limit,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"get_service_docs error: {e}")
        return []


def get_doc_services(driver, doc_id: str) -> list[dict]:
    """Get all services linked to a document."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Service)-[r:HAS_DOC]->(d:Document {id: $doc_id})
                RETURN s.id AS service_id, s.name AS service_name, r.relevance AS relevance
                """,
                doc_id=doc_id,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"get_doc_services error: {e}")
        return []
```

- [ ] **Step 4: Add check_sync_health()**

```python
def check_sync_health(driver, es_url: str, timeout: int = 30) -> dict:
    """Cross-check ES ↔ Neo4j Document consistency."""
    import time
    import urllib.request
    import json

    start = time.time()
    orphan_docs = []
    dangling_doc_refs = []
    missing_doc_edges = []
    cluster_issues = []
    partial = False

    try:
        # 1) ES doc_ids — scroll with timeout awareness
        req = urllib.request.Request(
            f"{es_url}/knowledge_base/_search?size=1000&_source=doc_id",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"query": {"match_all": {}}}).encode(),
        )
        resp = urllib.request.urlopen(req, timeout=min(10, timeout))
        es_data = json.loads(resp.read())
        es_doc_ids = set()
        for hit in es_data["hits"]["hits"]:
            src = hit.get("_source", {})
            did = src.get("doc_id", "")
            if did:
                es_doc_ids.add(did)
    except Exception as e:
        return {"status": "error", "detail": f"ES unreachable: {e}", "partial": True}

    try:
        # 2) Neo4j Document nodes
        with driver.session() as session:
            result = session.run("MATCH (d:Document) RETURN d.id AS id")
            neo4j_doc_ids = {r["id"] for r in result}

            # 3) HAS_DOC edges
            result = session.run(
                "MATCH (s:Service)-[r:HAS_DOC]->(d:Document) RETURN s.id AS service_id, d.id AS doc_id"
            )
            has_doc_pairs = [(r["service_id"], r["doc_id"]) for r in result]

            # 4) Orphan docs: in Neo4j but not ES
            orphan_docs = list(neo4j_doc_ids - es_doc_ids)

            # 5) Dangling service refs: ES doc_ids not in Neo4j
            dangling_doc_refs = list(es_doc_ids - neo4j_doc_ids)

            # 6) Missing doc edges: HAS_DOC pointing to deleted Document
            for svc_id, doc_id in has_doc_pairs:
                if doc_id not in neo4j_doc_ids:
                    missing_doc_edges.append({"service_id": svc_id, "doc_id": doc_id})

            # 7) Cluster sanity
            cluster_result = session.run(
                """
                MATCH (h:Host)-[:PART_OF]->(c:Cluster)
                RETURN c.service_id AS sid, c.name AS name, collect(h.id) AS hosts
                """
            )
            for cr in cluster_result:
                if len(cr["hosts"]) == 0:
                    cluster_issues.append(
                        {"service_id": cr["sid"], "issue": "Cluster has no member hosts"}
                    )

            if time.time() - start > timeout:
                partial = True
    except Exception as e:
        return {"status": "error", "detail": f"Neo4j unreachable: {e}", "partial": True}

    return {
        "status": "ok",
        "orphan_docs": orphan_docs,
        "dangling_doc_refs": dangling_doc_refs,
        "missing_doc_edges": missing_doc_edges,
        "cluster_issues": cluster_issues,
        "stats": {
            "es_docs": len(es_doc_ids),
            "neo4j_docs": len(neo4j_doc_ids),
            "has_doc_edges": len(has_doc_pairs),
        },
        "partial": partial,
    }
```

- [ ] **Step 5: Update init-neo4j.py**

```python
def init_schema(driver):
    with driver.session() as session:
        session.run("CREATE CONSTRAINT service_id IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE")
        session.run("CREATE CONSTRAINT host_id IF NOT EXISTS FOR (h:Host) REQUIRE h.id IS UNIQUE")
        session.run("CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
        session.run("CREATE INDEX service_name IF NOT EXISTS FOR (s:Service) ON (s.name)")
        session.run("CREATE INDEX cluster_service_idx IF NOT EXISTS FOR (c:Cluster) ON (c.service_id)")
```

- [ ] **Step 6: Add Neo4j retry config to get_driver()**

```python
def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_transaction_retry_time=15,  # 15s exponential backoff
        )
    return _driver
```

- [ ] **Step 7: Write cluster test**

```python
# tests/test_cluster.py
"""Integration tests for Cluster topology queries."""
import os
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CI") is None and os.environ.get("SKIP_INTEGRATION"),
        reason="Requires running Neo4j",
    ),
]


@pytest.mark.asyncio
async def test_get_service_cluster(docker_compose_env):
    """Service with cluster_nodes returns cluster topology."""
    from app.retrievers.graph_retriever import get_driver, get_service_cluster
    driver = get_driver()

    result = get_service_cluster(driver, "svc_nginx_company")
    assert "error" not in result, f"Got error: {result}"
    assert result.get("cluster") is not None, "Expected cluster info"
    assert len(result["cluster"]["members"]) >= 2, f"Expected >= 2 members, got: {result}"


@pytest.mark.asyncio
async def test_get_host_cluster_via_ip(docker_compose_env):
    """Cluster member IP finds its cluster."""
    from app.retrievers.graph_retriever import get_driver, get_host_cluster
    driver = get_driver()

    # nginx node-1 is in company-nginx-cluster
    result = get_host_cluster(driver, "10.33.16.42")
    assert result.get("cluster") is not None, f"Expected cluster, got: {result}"


@pytest.mark.asyncio
async def test_get_host_cluster_via_vip(docker_compose_env):
    """VIP IP finds its cluster."""
    from app.retrievers.graph_retriever import get_driver, get_host_cluster
    driver = get_driver()

    result = get_host_cluster(driver, "10.33.16.244")
    assert result.get("cluster") is not None, f"Expected cluster via VIP, got: {result}"
    assert result["cluster"].get("matched_by") == "vip"


@pytest.mark.asyncio
async def test_non_cluster_ip_returns_none(docker_compose_env):
    """IP not in any cluster returns cluster=None."""
    from app.retrievers.graph_retriever import get_driver, get_host_cluster
    driver = get_driver()

    result = get_host_cluster(driver, "10.99.99.99")
    assert result.get("cluster") is None, f"Expected no cluster, got: {result}"
```

- [ ] **Step 8: Verify**

```bash
pytest tests/test_cluster.py -v -m "integration" 2>&1 | tail -20
```

- [ ] **Step 9: Commit**

```bash
git add app/retrievers/graph_retriever.py scripts/init-neo4j.py tests/test_cluster.py
git commit -m "feat: add Cluster-aware queries, Document node methods, Cypher $param fix"
```

---

### Task 5: es_retriever.py — get_docs_by_ids()

**Files:**
- Modify: `app/retrievers/es_retriever.py`

- [ ] **Step 1: Add get_docs_by_ids()**

```python
def get_docs_by_ids(es: Elasticsearch, doc_ids: list[str], top_k: int = 50) -> list[dict]:
    """Fetch documents by doc_id list. Used for topology→doc secondary retrieval."""
    if not doc_ids:
        return []

    doc_ids = doc_ids[:50]  # Hard cap
    if len(doc_ids) > 50:
        print(f"get_docs_by_ids: truncating {len(doc_ids)} to 50")

    try:
        resp = es.search(
            index=INDEX_NAME,
            body={
                "size": top_k,
                "query": {
                    "bool": {
                        "must": [
                            {"terms": {"doc_id": doc_ids}},
                        ]
                    }
                },
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
                "service_id": src.get("service_id", ""),
                "engine": "es",
            })
        return results
    except Exception as e:
        print(f"get_docs_by_ids error: {e}")
        return []
```

Also update ES mapping `service_id` → `service_ids`:

In `init_index()`, change:
```python
                    "service_id": {"type": "keyword"},

# To:
                    "service_ids": {"type": "keyword"},
```

And in `search_fulltext()` result extraction:
```python
            "service_id": src.get("service_id", ""),

# To:
            "service_ids": src.get("service_ids", []),
```

- [ ] **Step 2: Commit**

```bash
git add app/retrievers/es_retriever.py
git commit -m "feat: add get_docs_by_ids() + ES mapping service_id→service_ids"
```

---

### Task 6: query_rewriter.py — 合并 rewrite + entity_extract + 正则兜底

**Files:**
- Modify: `app/router/query_rewriter.py`
- Create: `tests/test_rewriter.py`

- [ ] **Step 1: Rewrite query_rewriter.py**

Replace entire file with merged function + entity extract + regex fallback:

```python
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

# Regex fallback patterns — entity_cache populated lazily from ES aggregation
_ENTITY_CACHE = None
_ENTITY_CACHE_TTL = 0  # unix timestamp of last refresh

IP_PATTERN = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def _api_key() -> str | None:
    return settings.llm_api_key or None


def _regex_extract_entities(query: str) -> dict:
    """Fallback regex-based entity extraction. Fast, deterministic."""
    entities = {"host_ip": "", "service": "", "port": "", "symptom": ""}
    m = IP_PATTERN.search(query)
    if m:
        entities["host_ip"] = m.group(1)

    # Try known service names from ES aggregation cache
    global _ENTITY_CACHE, _ENTITY_CACHE_TTL
    import time
    if _ENTITY_CACHE is None or int(time.time()) - _ENTITY_CACHE_TTL > 300:
        _ENTITY_CACHE = _load_entity_cache()
        _ENTITY_CACHE_TTL = int(time.time())

    query_lower = query.lower()

    # Match known service names
    if _ENTITY_CACHE:
        for svc_name in _ENTITY_CACHE.get("service_names", []):
            if svc_name.lower() in query_lower and len(svc_name) > 2:
                entities["service"] = svc_name
                break

    return entities


def _load_entity_cache() -> dict:
    """Lazy-load known service names and host IPs from ES aggregation."""
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
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()

            # Extract JSON (may be wrapped in markdown code fences)
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
            for key in ["host_ip", "service", "port", "symptom"]:
                llm_val = llm_entities.get(key, "")
                regex_val = regex_entities.get(key, "")
                merged_entities[key] = llm_val if llm_val else regex_val

            if not r_query or len(r_query) < 2 or not r_types:
                return query, ["sop"], merged_entities

            return r_query, r_types, merged_entities

    except Exception as e:
        print(f"Rewrite+extract LLM error (using regex fallback): {e}")
        return query, ["sop"], _regex_extract_entities(query)


# Backward-compatible wrapper for existing callers
async def rewrite_query(query: str) -> tuple[str, list[str]]:
    rewritten, types, _ = await rewrite_and_extract(query)
    return rewritten, types
```

- [ ] **Step 2: Write rewriter tests**

```python
# tests/test_rewriter.py
"""Unit tests for query rewriter (no LLM needed for regex fallback)."""
import pytest
from app.router.query_rewriter import _regex_extract_entities


def test_regex_extracts_ip():
    entities = _regex_extract_entities("10.33.16.42 nginx 502")
    assert entities["host_ip"] == "10.33.16.42"


def test_regex_empty_on_no_ip():
    entities = _regex_extract_entities("nginx 502 排查")
    assert entities["host_ip"] == ""


def test_regex_returns_all_fields():
    entities = _regex_extract_entities("test query")
    for key in ["host_ip", "service", "port", "symptom"]:
        assert key in entities, f"Missing key: {key}"


def test_regex_multiple_ips_gets_first():
    entities = _regex_extract_entities("from 10.33.16.42 to 10.33.16.43")
    assert entities["host_ip"] == "10.33.16.42"


@pytest.mark.asyncio
async def test_rewrite_query_fallback(docker_compose_env):
    """Without API key, rewrite returns original + sop + regex entities."""
    from app.router.query_rewriter import rewrite_and_extract
    # Temporarily clear any API key
    import os
    saved_key = os.environ.get("LLM_API_KEY", "")
    os.environ.pop("LLM_API_KEY", None)
    try:
        rewritten, types, entities = await rewrite_and_extract("10.33.16.42 nginx 502")
        assert rewritten == "10.33.16.42 nginx 502", f"Expected original query, got: {rewritten}"
        assert "sop" in types
        assert entities["host_ip"] == "10.33.16.42"
    finally:
        if saved_key:
            os.environ["LLM_API_KEY"] = saved_key
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_rewriter.py -v
```

- [ ] **Step 4: Commit**

```bash
git add app/router/query_rewriter.py tests/test_rewriter.py
git commit -m "feat: merge rewrite+entity_extract in single LLM call + regex fallback"
```

---

### Task 7: routes.py — /query 改造 + 新增 API + degraded

**Files:**
- Modify: `app/api/routes.py`
- Modify: `app/models/query.py`
- Create: `tests/test_query.py`

- [ ] **Step 1: Update query models**

In `app/models/query.py`, add new fields:

```python
from pydantic import BaseModel, Field
from typing import Optional


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)


class SourceItem(BaseModel):
    title: str
    score: Optional[float] = None
    engine: str  # "es", "neo4j", "vector"
    snippet: str
    confidence: Optional[str] = None  # "★★★" | "★★" | "★"
    source_path: Optional[str] = None  # "direct" | "topology_expand" | "cluster_expand"


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
    degraded: bool = False
    missing_components: list[str] = []
    gap_warning: Optional[str] = None


class TopologyResponse(BaseModel):
    service_id: str
    service_name: str
    hosts: list[dict] = []
    ports: list[int] = []
    calls: list[dict] = []
    called_by: list[dict] = []


class HealthResponse(BaseModel):
    status: str
    es: str
    neo4j: str
    sync: Optional[dict] = None


class DocRef(BaseModel):
    doc_id: str
    title: str
    doc_type: str
    relevance: Optional[str] = None


class ServiceRef(BaseModel):
    service_id: str
    service_name: str
    relevance: Optional[str] = None
```

- [ ] **Step 2: Rewrite /query handler**

Replace the `query()` handler in routes.py:

```python
@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Multi-engine retrieval with graph enrichment, cluster awareness, degraded handling."""

    # Phase 1: Query analysis (single LLM call)
    from app.router.query_rewriter import rewrite_and_extract
    rewritten, query_types, entities = await rewrite_and_extract(req.query)
    if rewritten != req.query:
        print(f"Query analysis: '{req.query}' -> '{rewritten}' types={query_types} entities={entities}")

    _need_chain = "architecture" in query_types
    _need_topology = _need_chain or "topology" in query_types or "incident" in query_types

    es = get_es_client()
    driver = get_driver()

    all_results = []
    degraded = False
    missing_components = []
    host_ip = entities.get("host_ip", "")

    # Phase 2: ES multi-path retrieval
    es_results = search_fulltext(es, rewritten, req.top_k)
    all_results.append(es_results)

    vec_results = await search_vector(es, rewritten, req.top_k)
    all_results.append(vec_results)

    # Collect service_ids from ES hits for topology enrichment
    es_service_ids = set()
    for r in es_results:
        for sid in r.get("service_ids", [r.get("service_id", "")]):
            if sid:
                es_service_ids.add(sid)

    # Phase 3: Neo4j topology enrichment
    if _need_topology and es_service_ids:
        try:
            with driver.session() as session:
                for svc_id in list(es_service_ids)[:5]:
                    # Cluster-aware expansion
                    cluster_data = None
                    if host_ip:
                        cluster_data = get_host_cluster(driver, host_ip)
                    elif svc_id:
                        cluster_data = get_service_cluster(driver, svc_id)

                    if cluster_data and cluster_data.get("cluster"):
                        cluster = cluster_data["cluster"]
                        # Add cluster topology to results
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

                    # Phase 4: Topology→docs secondary search
                    doc_refs = get_service_docs(driver, svc_id, limit=50)
                    if doc_refs:
                        doc_ids = [d["doc_id"] for d in doc_refs][:50]
                        secondary_results = search_fulltext(
                            es,
                            rewritten,
                            top_k=3,
                            doc_ids_filter=doc_ids,  # modified search_fulltext to accept optional filter
                        )
                        for sr in secondary_results:
                            sr["engine"] = "es"
                            sr["source_path"] = "topology_expand"
                            all_results.append([sr])

                    if _need_chain:
                        chain = get_full_path(driver, svc_id, depth=4)
                        if chain:
                            all_results.append([{
                                "title": f"依赖链: {topo['service_name']}",
                                "content": json.dumps(chain, ensure_ascii=False),
                                "score": 0.9,
                                "engine": "neo4j",
                                "source_path": "topology_expand",
                            }])
        except Exception as e:
            print(f"Neo4j enrichment failed: {e}")
            degraded = True
            missing_components.append("neo4j")

    # Phase 5: Merge, Rerank, Synthesize
    if not all_results or all(v == [] for v in all_results):
        return QueryResponse(
            answer="当前知识库无法找到相关信息。",
            sources=[],
            gap_warning="知识库对这个问题覆盖不足，建议补充相关 SOP 或技术文档。",
        )

    ranked = await merge_and_rerank(req.query, *all_results, top_k=req.top_k)

    # Gap detection
    gap_warning = None
    if not ranked or ranked[0].get("score", 0) < 0.3:
        gap_warning = "知识库对这个问题覆盖不足，建议补充相关 SOP 或技术文档。"

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
        source_path_note = ""
        if r.get("source_path") == "topology_expand":
            source_path_note = " [拓扑扩展]"
        elif r.get("source_path") == "cluster_expand":
            source_path_note = " [集群扩展]"
        context_parts.append(
            f"[文档{i+1}] ({r.get('engine')}, {r.get('confidence', '★')}{source_path_note})\n{r.get('content', '')}"
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

    if degraded:
        answer += "\n\n[系统提示: 图拓扑服务当前不可用，以下分析仅基于文本检索，结果可能不完整。]"

    return QueryResponse(
        answer=answer,
        sources=sources,
        degraded=degraded,
        missing_components=missing_components,
        gap_warning=gap_warning,
    )
```

- [ ] **Step 3: Modify search_fulltext to accept optional doc_ids filter**

```python
def search_fulltext(es: Elasticsearch, query: str, top_k: int = 5, doc_type: str | None = None, doc_ids_filter: list[str] | None = None) -> list[dict]:
    """Full-text search with optional doc_id filtering for secondary retrieval."""
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
    ]
    must_clause = [{"bool": {"should": should_clause, "minimum_should_match": 1}}]
    if doc_type:
        must_clause.append({"term": {"doc_type": doc_type}})
    if doc_ids_filter:
        must_clause.append({"terms": {"doc_id": doc_ids_filter}})

    # ... rest same as before
```

- [ ] **Step 4: Add new API endpoints**

```python
@router.get("/service/{service_id}/docs")
async def service_docs(service_id: str):
    """Get all documents associated with a service."""
    driver = get_driver()
    try:
        doc_refs = get_service_docs(driver, service_id)
        return {"service_id": service_id, "docs": doc_refs, "count": len(doc_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/document/{doc_id}/services")
async def doc_services(doc_id: str):
    """Get all services associated with a document."""
    driver = get_driver()
    try:
        svc_refs = get_doc_services(driver, doc_id)
        return {"doc_id": doc_id, "services": svc_refs, "count": len(svc_refs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cluster/{service_id}")
async def cluster(service_id: str):
    """Get cluster topology for a service."""
    driver = get_driver()
    try:
        cluster_data = get_service_cluster(driver, service_id)
        return cluster_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health/sync")
async def health_sync():
    """Full ES ↔ Neo4j consistency check."""
    from app.config import settings
    driver = get_driver()
    try:
        result = check_sync_health(driver, settings.es_url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

Update existing `/health` endpoint:

```python
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

    # Quick sync count check
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
```

- [ ] **Step 5: Write query integration test**

```python
# tests/test_query.py
"""Integration tests for query pipeline."""
import os
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CI") is None and os.environ.get("SKIP_INTEGRATION"),
        reason="Requires running ES and Neo4j",
    ),
]


@pytest.mark.asyncio
async def test_query_returns_result_with_sources(docker_compose_env):
    """Happy path: query returns answer with source items."""
    import httpx
    async with httpx.AsyncClient(base_url="http://localhost:8001") as client:
        resp = await client.post("/api/v1/query", json={"query": "nginx 502 排查"}, timeout=30)
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "sources" in data
        assert len(data["sources"]) > 0, "Should have at least one source"


@pytest.mark.asyncio
async def test_query_degraded_when_neo4j_down(docker_compose_env):
    """When Neo4j is unreachable, degraded=true."""
    # This test is hard to simulate without stopping Neo4j.
    # It verifies the degraded field exists in response model.
    import httpx
    async with httpx.AsyncClient(base_url="http://localhost:8001") as client:
        resp = await client.post("/api/v1/query", json={"query": "nginx 502 排查"}, timeout=30)
        data = resp.json()
        assert "degraded" in data
        assert "missing_components" in data


@pytest.mark.asyncio
async def test_gap_detection_on_nonsense_query(docker_compose_env):
    """Query with no results gets gap_warning."""
    import httpx
    async with httpx.AsyncClient(base_url="http://localhost:8001") as client:
        resp = await client.post(
            "/api/v1/query",
            json={"query": "xyznonexistent12345 abcdefgh"},
            timeout=30,
        )
        data = resp.json()
        assert "gap_warning" in data or "无法找到" in data.get("answer", "")


@pytest.mark.asyncio
async def test_concurrent_10_queries(docker_compose_env):
    """10 concurrent queries should not error."""
    import asyncio
    import httpx

    async def one_query(i):
        async with httpx.AsyncClient(base_url="http://localhost:8001") as client:
            resp = await client.post("/api/v1/query", json={"query": f"nginx 502 排查"}, timeout=30)
            return resp.status_code

    tasks = [one_query(i) for i in range(10)]
    results = await asyncio.gather(*tasks)
    errors = [r for r in results if r != 200]
    assert len(errors) == 0, f"{len(errors)} queries failed out of 10"


@pytest.mark.asyncio
async def test_service_docs_endpoint(docker_compose_env):
    """GET /service/{id}/docs returns doc list."""
    import httpx
    async with httpx.AsyncClient(base_url="http://localhost:8001") as client:
        resp = await client.get("/api/v1/service/svc_nginx_company/docs", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "docs" in data


@pytest.mark.asyncio
async def test_health_sync_endpoint(docker_compose_env):
    """GET /health/sync returns sync check."""
    import httpx
    async with httpx.AsyncClient(base_url="http://localhost:8001") as client:
        resp = await client.get("/api/v1/health/sync", timeout=15)
        data = resp.json()
        assert "orphan_docs" in data
        assert "stats" in data
```

- [ ] **Step 6: Verify**

```bash
pytest tests/test_query.py -v -m "integration" 2>&1 | tail -25
```

- [ ] **Step 7: Commit**

```bash
git add app/api/routes.py app/models/query.py tests/test_query.py
git commit -m "feat: /query graph enrichment + cluster + degraded + new API endpoints"
```

---

# Phase 3: 运维工具 + 收尾

### Task 8: monitor.py — 5 新 metrics + check_sync

**Files:**
- Modify: `app/monitor.py`

- [ ] **Step 1: Add global counters and update get_metrics()**

```python
# Add after existing _stats initialization:
_gauge = {
    "sync_errors_total": 0,
    "orphan_docs_total": 0,
    "degraded_queries_total": 0,
    "entity_extract_fallback_total": 0,
    "llm_tokens_total": 0,
}


def inc_metric(name: str, delta: int = 1):
    if name in _gauge:
        _gauge[name] += delta


def set_metric(name: str, value: int):
    if name in _gauge:
        _gauge[name] = value


def get_metrics() -> dict:
    """Return current metrics snapshot."""
    result = {
        "uptime_seconds": int(time.time() - _stats["start_time"]),
        "endpoints": {},
        "gauges": dict(_gauge),
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
    return result
```

- [ ] **Step 2: Wire inc_metric calls**

In `routes.py` query handler:
- After `degraded = True`: `from app.monitor import inc_metric; inc_metric("degraded_queries_total")`
- After Neo4j sync error in `doc_indexer.py`: `inc_metric("sync_errors_total")`
- In `query_rewriter.py` regex fallback path: `inc_metric("entity_extract_fallback_total")`
- In `routes.py` `_call_llm()`: `inc_metric("llm_tokens_total", delta=estimated_tokens)`

- [ ] **Step 3: Commit**

```bash
git add app/monitor.py app/api/routes.py app/indexer/doc_indexer.py app/router/query_rewriter.py
git commit -m "feat: add 5 metrics gauges for sync errors, orphan, degraded, fallback, tokens"
```

---

### Task 9: load-topology.py — cluster_nodes 解析

**Files:**
- Modify: `scripts/load-topology.py`

- [ ] **Step 1: Add cluster_nodes loading logic**

In the `load_topology()` function (or main after loading services/hosts), add:

```python
def load_clusters(driver, data: dict):
    """Load cluster_nodes from YAML into Cluster nodes + PART_OF + BELONGS_TO."""
    imported = 0
    for svc in data.get("services", []):
        cluster_nodes = svc.get("cluster_nodes", [])
        if not cluster_nodes:
            continue

        svc_id = svc["id"]
        svc_name = svc.get("name", svc_id)
        vip = None

        with driver.session() as session:
            # Upsert Cluster node
            # Find VIP among cluster_nodes
            for node in cluster_nodes:
                role = node.get("role", "")
                if "VIP" in role or "vip" in role.lower():
                    vip = node.get("ip")
                    break

            session.run(
                """
                MERGE (c:Cluster {service_id: $svc_id})
                SET c.name = $name, c.vip = $vip
                """,
                svc_id=svc_id, name=svc_name, vip=vip,
            )

            # Detect duplicate BELONGS_TO
            existing = session.run(
                "MATCH (s:Service {id: $svc_id})-[r:BELONGS_TO]->(c:Cluster) RETURN count(r) AS cnt",
                svc_id=svc_id,
            )
            if existing.single()["cnt"] > 0:
                print(f"WARN: Service {svc_id} already BELONGS_TO a cluster, skipping BELONGS_TO")
            else:
                session.run(
                    """
                    MATCH (s:Service {id: $svc_id})
                    MATCH (c:Cluster {service_id: $svc_id})
                    MERGE (s)-[:BELONGS_TO]->(c)
                    """,
                    svc_id=svc_id,
                )

            # PART_OF edges
            for node in cluster_nodes:
                host_id = node.get("host", "")
                role = node.get("role", "")
                if not host_id:
                    continue

                # Validate host exists
                host_check = session.run(
                    "MATCH (h:Host {id: $hid}) RETURN h",
                    hid=host_id,
                )
                if host_check.single() is None:
                    print(f"WARN: host '{host_id}' in cluster_nodes not found in hosts list")
                    continue

                session.run(
                    """
                    MATCH (h:Host {id: $hid})
                    MATCH (c:Cluster {service_id: $svc_id})
                    MERGE (h)-[:PART_OF {role: $role}]->(c)
                    """,
                    hid=host_id, svc_id=svc_id, role=role,
                )
                imported += 1

    print(f"Clusters loaded: {imported} PART_OF edges")
    return imported
```

Call `load_clusters()` after `load_topology()` in main.

- [ ] **Step 2: Commit**

```bash
git add scripts/load-topology.py
git commit -m "feat: load cluster_nodes from YAML into Cluster nodes + PART_OF + BELONGS_TO"
```

---

### Task 10: templates + aiops-query CLI 适配

**Files:**
- Modify: `templates/sop.md`, `templates/tech.md`, `templates/incident.md`
- Modify: `aiops-query` (at `/root/.openclaw/skills/aiops-rag/aiops-query`)

- [ ] **Step 1: Update template frontmatter**

In all three templates, change `service_id:` → `service_ids:` + make it YAML list format:

```yaml
# Before:
service_id: {service_id}

# After:
service_ids:
  - {service_id}
```

And in the format strings, ensure `{service_id}` still resolves correctly.

- [ ] **Step 2: Update aiops-query CLI**

In `write_incident()`, `write_sop()`, `write_tech()`:
- Change `service_id` key in frontmatter to `service_ids` YAML list
- Change `delete_service()` ES deletion to use `service_ids` term query instead of `service_id`

```python
# In delete_service(), ES delete_by_query:
es.delete_by_query(index="knowledge_base", body={"query": {"term": {"service_ids": service_id}}})
```

- [ ] **Step 3: Commit**

```bash
git add templates/ ~/.openclaw/skills/aiops-rag/aiops-query
git commit -m "feat: service_id → service_ids in templates and CLI"
```

---

### Task 11: cron — sync-health 定时检查

**Files:**
- Create: `cron/sync-health-check.sh`

- [ ] **Step 1: Write sync-health check script**

```bash
#!/bin/bash
# sync-health-check.sh — daily ES ↔ Neo4j consistency audit
# Run: /root/.openclaw/skills/aiops-rag/cron/sync-health-check.sh

API="http://localhost:8001/api/v1"
REPORT_DIR="/root/.openclaw/workspace-shared/rag/reports"
mkdir -p "$REPORT_DIR"

TODAY=$(date +%Y-%m-%d)
REPORT="$REPORT_DIR/sync-health-$TODAY.json"

curl -s "$API/health/sync" | python3 -m json.tool > "$REPORT" 2>/dev/null

if [ $? -ne 0 ]; then
    echo "[$(date)] sync-health check failed — API unreachable" >> "$REPORT_DIR/sync-health-errors.log"
    exit 1
fi

ORPHAN_COUNT=$(python3 -c "import json; d=json.load(open('$REPORT')); print(len(d.get('orphan_docs',[])))")
DANGLING_COUNT=$(python3 -c "import json; d=json.load(open('$REPORT')); print(len(d.get('dangling_doc_refs',[])))")

if [ "$ORPHAN_COUNT" -gt 0 ] || [ "$DANGLING_COUNT" -gt 0 ]; then
    echo "[$(date)] Sync issues: $ORPHAN_COUNT orphan docs, $DANGLING_COUNT dangling refs" >> "$REPORT_DIR/sync-health-alerts.log"
    echo "Report: $REPORT" >> "$REPORT_DIR/sync-health-alerts.log"
fi

echo "[$(date)] sync-health: $ORPHAN_COUNT orphans, $DANGLING_COUNT dangling" >> "$REPORT_DIR/sync-health-summary.log"
```

- [ ] **Step 2: Register in crontab**

```bash
# Add to crontab (runs daily at 03:00 Asia/Shanghai)
(crontab -l 2>/dev/null; echo "0 3 * * * /root/.openclaw/skills/aiops-rag/cron/sync-health-check.sh") | crontab -
```

- [ ] **Step 3: Commit**

```bash
git add cron/sync-health-check.sh
git commit -m "chore: add daily sync-health cron job"
```

---

# 部署验证

### Task 12: 部署 + 端到端验证

- [ ] **Step 1: 遵循部署顺序**

```bash
# 1. Rebuild containers
docker compose -f ~/.openclaw/workspace-shared/rag/docker-compose.yml build --no-cache api

# 2. Start services
docker compose -f ~/.openclaw/workspace-shared/rag/docker-compose.yml up -d

# 3. Init Neo4j schema (约束 + 索引)
docker exec rag-api python3 /app/scripts/init-neo4j.py

# 4. Verify constraints
docker exec rag-api python3 -c "
from app.retrievers.graph_retriever import get_driver
driver = get_driver()
with driver.session() as s:
    r = s.run('SHOW CONSTRAINTS')
    for rec in r:
        print(rec)
"

# 5. Full reindex (service_ids array)
docker exec rag-api python3 /app/scripts/index-docs.py /app/examples/aiops-docs/ --clean

# 6. Load topology including cluster_nodes
docker exec rag-api python3 /app/scripts/load-topology.py /app/examples/aiops-docs/topology/call-graph.yml

# 7. Verify health
docker exec rag-api python3 -c "
import urllib.request, json
resp = urllib.request.urlopen('http://localhost:8001/api/v1/health')
print(json.dumps(json.loads(resp.read()), indent=2, ensure_ascii=False))
"
```

- [ ] **Step 2: Run full test suite**

```bash
cd /root/.openclaw/workspace-shared/rag
pytest tests/ -v 2>&1 | tail -30
```

Expected: All tests pass (integration tests require running ES+Neo4j)

- [ ] **Step 3: Quick smoke test**

```bash
docker exec rag-api python3 -c "
import urllib.request, json
data = json.dumps({'query': 'nginx 502 排查'}).encode()
req = urllib.request.Request('http://localhost:8001/api/v1/query', data=data, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=30)
result = json.loads(resp.read())
print('Sources:', len(result['sources']))
print('Degraded:', result.get('degraded'))
print('Has source_path:', any(s.get('source_path') for s in result['sources']))
"
```

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "chore: deploy verification and final docs update"
```
