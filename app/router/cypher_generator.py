"""Dynamic Cypher generation via LLM. Ref: doc/Neo4j知识图谱 — GraphCypherQAChain.
Fallback: pre-defined templates for common query patterns."""

import json

import httpx

from app.config import settings

CYPHER_SYSTEM_PROMPT = """You are a Neo4j Cypher expert. Given a graph schema and a question, generate a Cypher query.

Schema:
- (Service {id, name, status, description})
- (Host {id, name, ip, os})
- (Port {number, protocol})
- (Service)-[:DEPLOYS_ON]->(Host)
- (Host)-[:HAS_PORT]->(Port)
- (Service)-[:CALLS {protocol, port}]->(Service)

Rules:
1. Return ONLY the Cypher query, no explanation
2. Use parameterized queries ($param) not string interpolation
3. Always add LIMIT to prevent full scan
4. Case-insensitive matching: use toLower() or CONTAINS
5. For path queries use MATCH path = ... RETURN path

Examples:
Q: Which services are deployed on host prod-app-01?
A: MATCH (s:Service)-[:DEPLOYS_ON]->(h:Host {id: $hid}) RETURN s.id, s.name

Q: What is the full dependency chain of order-service?
A: MATCH path = (s:Service {id: $sid})-[:CALLS*1..5]->(down) RETURN path LIMIT 50

Q: If host prod-app-01 fails, which services are affected?
A: MATCH (h:Host {id: $hid})<-[:DEPLOYS_ON]-(s:Service) OPTIONAL MATCH (s)-[:CALLS*1..3]->(down) RETURN DISTINCT s.id, s.name, down.id, down.name

Q: Are there any circular dependencies?
A: MATCH (s:Service)-[:CALLS*2..]->(s) RETURN DISTINCT s.id, s.name
"""


async def generate_cypher(question: str) -> str | None:
    """Generate Cypher query from natural language. Returns None on failure."""
    key = settings.llm_api_key
    if not key:
        return None

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": CYPHER_SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0,
        "max_tokens": 300,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Cypher generation error: {e}")
        return None
