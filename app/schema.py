"""Schema validation for topology YAML, frontmatter, and Neo4j properties."""

# --- Neo4j node property whitelists ---

SERVICE_PROPS = {"id", "name", "status", "description"}
HOST_PROPS = {"id", "name", "ip", "os"}
PORT_PROPS = {"number", "protocol", "status"}
CALL_PROPS = {"protocol", "port"}
DOC_PROPS = {"id", "title", "type", "updated_at"}
CLUSTER_PROPS = {"service_id", "name", "vip"}
PART_OF_PROPS = {"role"}
HAS_DOC_PROPS = {"doc_type", "relevance"}

ALLOWED_PROPS = {
    "Service": SERVICE_PROPS,
    "Host": HOST_PROPS,
    "Port": PORT_PROPS,
    "Document": DOC_PROPS,
    "Cluster": CLUSTER_PROPS,
}

VALID_RELEVANCE = {"primary", "secondary", "mentioned"}

# --- YAML topology JSON Schema ---

TOPOLOGY_SCHEMA = {
    "type": "object",
    "required": ["services", "hosts"],
    "properties": {
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "string", "pattern": "^(svc_|host_|port_)[a-z0-9_]+$"},
                    "name": {"type": "string", "minLength": 1},
                    "deploys_on": {"type": "string"},
                    "calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["target"],
                            "properties": {
                                "target": {"type": "string"},
                                "protocol": {"type": "string"},
                                "port": {"type": "integer"},
                            },
                        },
                    },
                    "ports": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
        "hosts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "string", "pattern": "^(host_|svc_|port_)[a-z0-9_]+$"},
                    "name": {"type": "string", "minLength": 1},
                    "ip": {"type": "string"},
                    "os": {"type": "string"},
                },
            },
        },
    },
}

# --- Frontmatter validation ---

VALID_DOC_TYPES = {"sop", "tech", "incident"}

FRONTMATTER_REQUIRED = ["title", "doc_type", "service_ids"]


def validate_frontmatter(meta: dict) -> list[str]:
    """Validate frontmatter fields. Returns list of error messages."""
    errors = []
    for field in FRONTMATTER_REQUIRED:
        if field not in meta or not meta[field]:
            errors.append(f"缺少必填字段: {field}")

    doc_type = meta.get("doc_type", "")
    if doc_type and doc_type not in VALID_DOC_TYPES:
        errors.append(f"无效 doc_type '{doc_type}'. 合法值: {', '.join(VALID_DOC_TYPES)}")

    service_ids = meta.get("service_ids", [])
    if not isinstance(service_ids, list) or len(service_ids) == 0:
        errors.append("service_ids 不能为空数组，至少需要一个 service_id")
    else:
        for sid in service_ids:
            if not isinstance(sid, str) or not sid.startswith("svc_"):
                errors.append(f"service_id '{sid}' 应以 'svc_' 开头")

    return errors


def validate_topology(data: dict) -> list[str]:
    """Validate topology YAML structure. Returns list of error messages."""
    errors = []

    service_ids = set()
    host_ids = set()

    for i, s in enumerate(data.get("services", [])):
        sid = s.get("id", f"<missing-{i}>")
        if sid in service_ids:
            errors.append(f"services[{i}]: 重复 service_id '{sid}'")
        service_ids.add(sid)

        if not s.get("name"):
            errors.append(f"services[{i}] ({sid}): 缺少 name")

        host_id = s.get("deploys_on")
        if host_id and not host_id.startswith("host_"):
            errors.append(f"services[{i}] ({sid}): deploys_on '{host_id}' 应以 'host_' 开头")

        for j, call in enumerate(s.get("calls", [])):
            if not call.get("target"):
                errors.append(f"services[{i}] ({sid}) calls[{j}]: 缺少 target")

    for i, h in enumerate(data.get("hosts", [])):
        hid = h.get("id", f"<missing-{i}>")
        if hid in host_ids:
            errors.append(f"hosts[{i}]: 重复 host_id '{hid}'")
        host_ids.add(hid)

        if not h.get("name"):
            errors.append(f"hosts[{i}] ({hid}): 缺少 name")

    return errors


def validate_cross_refs(data: dict) -> list[str]:
    """Cross-reference validation: every deploys_on and calls target must exist."""
    errors = []

    service_ids = {s["id"] for s in data.get("services", []) if s.get("id")}
    host_ids = {h["id"] for h in data.get("hosts", []) if h.get("id")}

    for s in data.get("services", []):
        sid = s.get("id", "?")
        host_id = s.get("deploys_on", "")
        if host_id and host_id not in host_ids:
            errors.append(f"{sid}.deploys_on='{host_id}' 不存在于 hosts 列表中")

        for j, call in enumerate(s.get("calls", [])):
            target = call.get("target", "")
            if target and target not in service_ids:
                errors.append(f"{sid}.calls[{j}].target='{target}' 不存在于 services 列表中")

    return errors
