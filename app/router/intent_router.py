"""Intent classification + routing. Rules first, LLM fallback."""

from typing import Literal

QueryType = Literal["sop", "topology", "incident", "hybrid"]

# Keyword rules for quick classification
_TOPOLOGY_KEYWORDS = ["部署", "依赖", "调用", "拓扑", "主机", "端口", "在哪", "关系", "拓扑图"]
_SOP_KEYWORDS = ["怎么", "如何", "步骤", "SOP", "流程", "操作", "重启", "扩容", "发布", "部署"]
_INCIDENT_KEYWORDS = ["故障", "报错", "502", "500", "连不上", "超时", "宕机", "挂掉", "异常", "排查", "告警"]


def classify_intent(query: str) -> QueryType:
    """Classify query intent by keyword rules."""
    q = query.lower()

    has_topology = any(kw in q for kw in _TOPOLOGY_KEYWORDS)
    has_sop = any(kw in q for kw in _SOP_KEYWORDS)
    has_incident = any(kw in q for kw in _INCIDENT_KEYWORDS)

    score_map = {
        "topology": sum(kw in q for kw in _TOPOLOGY_KEYWORDS),
        "sop": sum(kw in q for kw in _SOP_KEYWORDS),
        "incident": sum(kw in q for kw in _INCIDENT_KEYWORDS),
    }

    # If multiple types match, it's hybrid
    matched_types = [t for t, s in score_map.items() if s > 0]
    if len(matched_types) >= 2:
        return "hybrid"

    if matched_types:
        return matched_types[0]

    # Default: hybrid (use all engines)
    return "hybrid"


def get_routing_strategy(intent: QueryType) -> dict:
    """Return which engines to call based on intent."""
    strategies = {
        "sop": {"es": True, "vector": True, "neo4j": False},
        "topology": {"es": False, "vector": False, "neo4j": True},
        "incident": {"es": True, "vector": True, "neo4j": True},
        "hybrid": {"es": True, "vector": True, "neo4j": True},
    }
    return strategies.get(intent, strategies["hybrid"])
