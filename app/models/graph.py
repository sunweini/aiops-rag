from pydantic import BaseModel
from typing import Optional


class ServiceNode(BaseModel):
    id: str
    name: str
    status: str = "unknown"
    description: str = ""


class HostNode(BaseModel):
    id: str
    name: str
    ip: str = ""
    os: str = ""


class PortNode(BaseModel):
    number: int
    protocol: str = "tcp"
    status: str = "unknown"


class CallRelation(BaseModel):
    source_id: str
    target_id: str
    protocol: str = ""
    port: int = 0
