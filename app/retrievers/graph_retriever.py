from neo4j import GraphDatabase
from app.config import settings

"""Neo4j graph queries for topology. Ref: doc/Neo4j知识图谱.

NOTE: Some Cypher queries use f-string interpolation for depth parameters.
All interpolated values are strictly integer types (never user input) to
avoid Cypher injection risk."""


_driver = None

def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_transaction_retry_time=15,
        )
    return _driver

def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


def init_schema(driver):
    with driver.session() as session:
        session.run("CREATE CONSTRAINT service_id IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE")
        session.run("CREATE CONSTRAINT host_id IF NOT EXISTS FOR (h:Host) REQUIRE h.id IS UNIQUE")
        session.run("CREATE CONSTRAINT host_ip IF NOT EXISTS FOR (h:Host) REQUIRE h.ip IS UNIQUE")
        session.run("CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
        session.run("CREATE INDEX service_name IF NOT EXISTS FOR (s:Service) ON (s.name)")
        session.run("CREATE INDEX cluster_service_idx IF NOT EXISTS FOR (c:Cluster) ON (c.service_id)")


def get_service_topology(driver, service_id: str) -> dict:
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Service {id: $sid})
                OPTIONAL MATCH (s)-[:DEPLOYS_ON]->(h:Host)
                OPTIONAL MATCH (h)-[:HAS_PORT]->(p:Port)
                RETURN s.id AS service_id, s.name AS service_name,
                       collect(DISTINCT {host_id: h.id, host_name: h.name, ip: h.ip}) AS hosts,
                       collect(DISTINCT p.number) AS ports
                """,
                sid=service_id,
            )
            service_info = result.single()
            if not service_info:
                return {"service_id": service_id, "error": "not found"}

            calls_result = session.run(
                """
                MATCH (s:Service {id: $sid})-[r:CALLS]->(target:Service)
                RETURN target.id AS id, target.name AS name,
                       r.protocol AS protocol, r.port AS port
                """,
                sid=service_id,
            )
            calls = [dict(r) for r in calls_result]

            called_by_result = session.run(
                """
                MATCH (target:Service {id: $sid})<-[r:CALLS]-(source:Service)
                RETURN source.id AS id, source.name AS name
                """,
                sid=service_id,
            )
            called_by = [dict(r) for r in called_by_result]

            hosts = [h for h in service_info["hosts"] if h.get("host_id")]
            ports = list(set([p for p in service_info["ports"] if p is not None]))

            return {
                "service_id": service_info["service_id"],
                "service_name": service_info["service_name"],
                "hosts": hosts,
                "ports": ports,
                "calls": calls,
                "called_by": called_by,
            }
    except Exception as e:
        print(f"Neo4j topology error: {e}")
        return {"service_id": service_id, "error": "db_error"}


def get_host_services(driver, host_id: str) -> list[dict]:
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Host {id: $hid})<-[:DEPLOYS_ON]-(s:Service)
                RETURN s.id AS id, s.name AS name, s.status AS status
                """,
                hid=host_id,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"Neo4j host services error: {e}")
        return []


def get_service_downstream(driver, service_id: str, depth: int = 2) -> list[dict]:
    try:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (s:Service {{id: $sid}})-[:CALLS*1..{int(depth)}]->(down:Service)
                RETURN DISTINCT down.id AS id, down.name AS name
                """,
                sid=service_id,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"Neo4j downstream error: {e}")
        return []


def get_host_impact(driver, host_id: str) -> dict:
    """Multi-hop impact analysis: if host fails, which services directly affected + their downstreams.
    Ref: doc/Graph RAG进阶 — 多跳推理"""
    try:
        with driver.session() as session:
            # Direct services on this host
            direct = session.run(
                """
                MATCH (h:Host {id: $hid})<-[:DEPLOYS_ON]-(s:Service)
                RETURN s.id AS id, s.name AS name, 'direct' AS impact
                """, hid=host_id,
            )
            direct_services = [dict(r) for r in direct]

            # Downstream services 1-3 hops from affected services
            downstream = session.run(
                """
                MATCH (h:Host {id: $hid})<-[:DEPLOYS_ON]-(s:Service)
                WITH s
                MATCH (s)-[:CALLS*1..3]->(down:Service)
                RETURN DISTINCT down.id AS id, down.name AS name, 'downstream' AS impact
                LIMIT 50
                """, hid=host_id,
            )
            downstream_services = [dict(r) for r in downstream]

            # Upstream services that call affected services
            upstream = session.run(
                """
                MATCH (h:Host {id: $hid})<-[:DEPLOYS_ON]-(s:Service)
                WITH s
                MATCH (up:Service)-[:CALLS]->(s)
                RETURN DISTINCT up.id AS id, up.name AS name, 'upstream' AS impact
                LIMIT 20
                """, hid=host_id,
            )
            upstream_services = [dict(r) for r in upstream]

            return {
                "host_id": host_id,
                "impact_summary": f"{len(direct_services)} affected, {len(downstream_services)} downstream, {len(upstream_services)} upstream",
                "direct": direct_services,
                "downstream": downstream_services,
                "upstream": upstream_services,
            }
    except Exception as e:
        print(f"Neo4j impact error: {e}")
        return {"host_id": host_id, "error": "db_error"}


def get_full_path(driver, service_id: str, depth: int = 5) -> list[dict]:
    """Full dependency path: complete chain of Service->[...]->Service.
    Note: depth value must be literal (Neo4j restriction on param in var-length)."""
    try:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH path = (s:Service {{id: $sid}})-[:CALLS*1..{int(depth)}]->(down:Service)
                WITH nodes(path) AS nodes, relationships(path) AS rels
                UNWIND range(0, size(rels)-1) AS i
                RETURN nodes[i].id AS from_id, nodes[i].name AS from_name,
                       type(rels[i]) AS rel,
                       rels[i].protocol AS protocol, rels[i].port AS port,
                       nodes[i+1].id AS to_id, nodes[i+1].name AS to_name
                LIMIT 50
                """, sid=service_id,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"Neo4j path error: {e}")
        return []


def detect_circular_deps(driver) -> list[dict]:
    """Detect circular service dependencies."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Service)-[:CALLS*2..]->(s)
                RETURN DISTINCT s.id AS id, s.name AS name
                LIMIT 20
                """,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"Neo4j circular dep error: {e}")
        return []


# ── Document sync (T3) ───────────────────────────────────────────────

def sync_document_node(driver, doc: dict) -> dict:
    """Upsert Document node + HAS_DOC edges (Service→Doc + Host→Doc) in Neo4j.
    Returns {'status': 'ok'|'partial_success'|'error', 'detail': str}."""
    doc_id = doc.get("doc_id", "")
    title = doc.get("title", "")
    doc_type = doc.get("doc_type", "tech")
    updated_at = doc.get("updated_at", "")
    service_ids = doc.get("service_ids", [])
    host_ids = doc.get("host_ids", [])
    relevance = "primary"

    if not doc_id or not service_ids:
        return {"status": "error", "detail": f"Missing doc_id or service_ids: {doc_id}"}

    services_exist = 0
    services_missing = 0
    hosts_exist = 0
    hosts_missing = 0

    try:
        with driver.session() as session:
            session.run(
                """
                MERGE (d:Document {id: $doc_id})
                SET d.title = $title, d.type = $doc_type, d.updated_at = $updated_at
                """,
                doc_id=doc_id, title=title, doc_type=doc_type, updated_at=updated_at,
            )

            # Service → Document edges
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

            # Host → Document edges
            for hid in host_ids:
                result = session.run(
                    "MATCH (h:Host {id: $hid}) RETURN h",
                    hid=hid,
                )
                if result.single() is None:
                    hosts_missing += 1
                    print(f"Host '{hid}' not found in Neo4j — skipping HAS_DOC edge")
                    continue

                session.run(
                    """
                    MATCH (h:Host {id: $hid})
                    MATCH (d:Document {id: $doc_id})
                    MERGE (h)-[:HAS_DOC {doc_type: $doc_type, relevance: $relevance}]->(d)
                    """,
                    hid=hid, doc_id=doc_id, doc_type=doc_type, relevance=relevance,
                )
                hosts_exist += 1
    except Exception as e:
        print(f"sync_document_node error: {e}")
        return {"status": "error", "detail": str(e)}

    total_ok = services_exist + hosts_exist
    total_missing = services_missing + hosts_missing
    if total_missing > 0 and total_ok > 0:
        parts = []
        if services_exist: parts.append(f"{services_exist} service")
        if hosts_exist: parts.append(f"{hosts_exist} host")
        missing_parts = []
        if services_missing: missing_parts.append(f"{services_missing} service")
        if hosts_missing: missing_parts.append(f"{hosts_missing} host")
        return {"status": "partial_success", "detail": f"{', '.join(parts)} edges ok; {', '.join(missing_parts)} not found"}
    elif total_ok == 0:
        return {"status": "error", "detail": f"0 edges created, {total_missing} nodes not found"}
    return {"status": "ok", "detail": f"{services_exist} services + {hosts_exist} hosts edges created"}


def update_host(driver, host_id: str, props: dict) -> dict:
    """Update Host node properties."""
    return update_node(driver, "Host", "id", host_id, props)


def update_node(driver, label: str, id_field: str, node_id: str, props: dict) -> dict:
    """Update any Neo4j node properties. Validated against ALLOWED_PROPS.
    Returns {'status': 'ok'|'error', 'detail': dict|str}."""
    from app.schema import ALLOWED_PROPS
    whitelist = ALLOWED_PROPS.get(label, set())
    valid = {k: v for k, v in props.items() if k in whitelist and v is not None}
    if not valid:
        return {"status": "error", "detail": f"No valid properties for {label} in: {list(props.keys())}"}

    setters = ", ".join(f"n.{k} = ${k}" for k in valid)
    try:
        with driver.session() as session:
            query = f"MATCH (n:{label} {{{id_field}: $nid}}) SET {setters} RETURN properties(n) AS props"
            result = session.run(query, nid=node_id, **valid)
            record = result.single()
            if not record:
                return {"status": "error", "detail": f"{label} '{node_id}' not found"}
            return {"status": "ok", "detail": record["props"]}
    except Exception as e:
        print(f"update_node({label}) error: {e}")
        return {"status": "error", "detail": str(e)}


def delete_document_node(driver, doc_id: str) -> dict:
    """Delete HAS_DOC edges and remove Document node if orphaned.
    Returns {'status': 'ok'|'error', 'detail': str}."""
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH ()-[r:HAS_DOC]->(d:Document {id: $doc_id}) RETURN count(r) AS cnt",
                doc_id=doc_id,
            )
            edge_count = result.single()["cnt"]

            if edge_count == 0:
                session.run(
                    "MATCH (d:Document {id: $doc_id}) DELETE d",
                    doc_id=doc_id,
                )
                return {"status": "ok", "detail": f"Document {doc_id} deleted (no edges)"}

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


# ── Doc queries (T4) ──────────────────────────────────────────────────

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


def get_doc_hosts(driver, doc_id: str) -> list[dict]:
    """Get all hosts linked to a document."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Host)-[r:HAS_DOC]->(d:Document {id: $doc_id})
                RETURN h.id AS host_id, h.name AS host_name, h.ip AS ip, r.relevance AS relevance
                """,
                doc_id=doc_id,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"get_doc_hosts error: {e}")
        return []


def get_host_docs(driver, host_id: str, limit: int = 50) -> list[dict]:
    """Get all doc references for a host, sorted by updated_at DESC."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Host {id: $hid})-[r:HAS_DOC]->(d:Document)
                RETURN d.id AS doc_id, d.title AS title, d.type AS doc_type,
                       d.updated_at AS updated_at, r.relevance AS relevance
                ORDER BY d.updated_at DESC
                LIMIT $limit
                """,
                hid=host_id, limit=limit,
            )
            return [dict(r) for r in result]
    except Exception as e:
        print(f"get_host_docs error: {e}")
        return []


def get_service_cluster(driver, service_id: str) -> dict:
    """Get cluster topology for a service. Expands all PART_OF members."""
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Service {id: $sid})-[:DEPLOYS_ON]->(h:Host)-[:PART_OF]->(c:Cluster)
                RETURN c.service_id AS cluster_service_id, c.name AS cluster_name,
                       c.vip AS vip
                """,
                sid=service_id,
            )
            records = list(result)
            if len(records) > 1:
                print(f"WARN: Multiple clusters for service {service_id}, using first")
            if not records:
                return {"service_id": service_id, "cluster": None}
            r = records[0]

            members_result = session.run(
                """
                MATCH (h:Host)-[p:PART_OF]->(c:Cluster {service_id: $sid})
                RETURN h.id AS host_id, h.name AS host_name, h.ip AS ip, p.role AS role
                """,
                sid=service_id,
            )
            members = [dict(m) for m in members_result]
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
                vip_result = session.run(
                    "MATCH (c:Cluster {vip: $ip}) RETURN c.service_id AS cluster_service_id, c.name AS cluster_name",
                    ip=host_ip,
                )
                vip_record = vip_result.single()
                if not vip_record:
                    return {"host_ip": host_ip, "cluster": None}
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


def check_sync_health(driver, es_url: str, timeout: int = 30) -> dict:
    """Cross-check ES ↔ Neo4j Document consistency."""
    import time
    import urllib.request
    import json

    start = time.time()
    es_doc_ids = set()
    partial = False

    try:
        req = urllib.request.Request(
            f"{es_url}/knowledge_base/_search?size=1000",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"query": {"match_all": {}}}).encode(),
        )
        resp = urllib.request.urlopen(req, timeout=min(10, timeout))
        es_data = json.loads(resp.read())
        for hit in es_data["hits"]["hits"]:
            src = hit.get("_source", {})
            did = src.get("doc_id", "")
            if did:
                es_doc_ids.add(did)
    except Exception as e:
        return {"status": "error", "detail": f"ES unreachable: {e}", "partial": True}

    try:
        with driver.session() as session:
            result = session.run("MATCH (d:Document) RETURN d.id AS id")
            neo4j_doc_ids = {r["id"] for r in result}

            result = session.run(
                "MATCH (s:Service)-[r:HAS_DOC]->(d:Document) RETURN s.id AS service_id, d.id AS doc_id"
            )
            has_doc_pairs = [(r["service_id"], r["doc_id"]) for r in result]

            orphan_docs = sorted(neo4j_doc_ids - es_doc_ids)
            dangling_doc_refs = sorted(es_doc_ids - neo4j_doc_ids)

            missing_doc_edges = []
            for svc_id, doc_id in has_doc_pairs:
                if doc_id not in neo4j_doc_ids:
                    missing_doc_edges.append({"service_id": svc_id, "doc_id": doc_id})

            cluster_issues = []
            cluster_result = session.run(
                """
                MATCH (h:Host)-[:PART_OF]->(c:Cluster)
                RETURN c.service_id AS sid, c.name AS name, collect(h.id) AS hosts
                """
            )
            for cr in cluster_result:
                if len(cr["hosts"]) == 0:
                    cluster_issues.append({
                        "service_id": cr["sid"],
                        "issue": "Cluster has no member hosts",
                    })

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
