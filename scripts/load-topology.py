#!/usr/bin/env python3
"""Load topology YAML into Neo4j. Two-pass: nodes first, then relationships."""

import sys
import yaml
sys.path.insert(0, "/app")

from app.retrievers.graph_retriever import get_driver


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

        # Find VIP among cluster_nodes
        for node in cluster_nodes:
            role = node.get("role", "")
            if "VIP" in role or "vip" in role.lower():
                vip = node.get("ip")
                break

        with driver.session() as session:
            session.run(
                """
                MERGE (c:Cluster {service_id: $svc_id})
                SET c.name = $name, c.vip = $vip
                """,
                svc_id=svc_id, name=svc_name, vip=vip,
            )

            # Check for duplicate BELONGS_TO
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


def load_topology(filepath: str):
    with open(filepath, "r") as f:
        data = yaml.safe_load(f)

    # Validate schema
    from app.schema import validate_topology, validate_cross_refs
    errors = validate_topology(data) + validate_cross_refs(data)
    if errors:
        print("拓扑文件校验失败:")
        for e in errors:
            print(f"  - {e}")
        return

    # Property whitelist validation
    from app.schema import HOST_PROPS, SERVICE_PROPS
    for h in data.get("hosts", []):
        unknown = set(h.keys()) - HOST_PROPS
        if unknown:
            print(f"  警告: host '{h['id']}' 包含未知字段将被忽略: {unknown}")
    for s in data.get("services", []):
        unknown = set(s.keys()) - SERVICE_PROPS - {"deploys_on", "calls", "ports", "cluster_nodes"}
        if unknown:
            print(f"  警告: service '{s['id']}' 包含未知字段将被忽略: {unknown}")

    driver = get_driver()

    with driver.session() as session:
        # Clear existing
        session.run("MATCH (n) DETACH DELETE n")

        services = data.get("services", [])
        hosts = data.get("hosts", [])

        # PASS 1: Create all nodes
        for h in hosts:
            session.run(
                "MERGE (host:Host {id: $id}) SET host.name = $name, host.ip = $ip, host.os = $os",
                id=h["id"], name=h.get("name", ""), ip=h.get("ip", ""), os=h.get("os", ""),
            )

        for s in services:
            session.run(
                "MERGE (svc:Service {id: $id}) SET svc.name = $name",
                id=s["id"], name=s.get("name", ""),
            )

        # PASS 2: Create relationships
        for s in services:
            svc_id = s["id"]
            host_id = s.get("deploys_on")

            # DEPLOYS_ON
            if host_id:
                session.run(
                    "MATCH (svc:Service {id: $sid}), (host:Host {id: $hid}) MERGE (svc)-[:DEPLOYS_ON]->(host)",
                    sid=svc_id, hid=host_id,
                )

            # HAS_PORT
            for port_num in s.get("ports", []):
                if host_id:
                    session.run(
                        "MATCH (host:Host {id: $hid}) MERGE (p:Port {number: $port}) SET p.protocol = $protocol MERGE (host)-[:HAS_PORT]->(p)",
                        hid=host_id, port=port_num, protocol="tcp",
                    )

            # CALLS
            for call in s.get("calls", []):
                session.run(
                    "MATCH (src:Service {id: $src_id}), (tgt:Service {id: $tgt_id}) MERGE (src)-[:CALLS {protocol: $protocol, port: $port}]->(tgt)",
                    src_id=svc_id, tgt_id=call["target"],
                    protocol=call.get("protocol", ""), port=call.get("port", 0),
                )

    load_clusters(driver, data)

    from app.retrievers.graph_retriever import close_driver
    close_driver()
    print(f"Topology loaded from {filepath}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: load-topology.py <call-graph.yml>")
        sys.exit(1)
    load_topology(sys.argv[1])
