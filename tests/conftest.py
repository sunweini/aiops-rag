"""Shared test fixtures."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
def docker_compose_env():
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
    return [
        {"id": "svc_nginx_company", "name": "company-nginx-cluster", "status": "active"},
        {"id": "svc_order", "name": "order-service", "status": "active"},
    ]
