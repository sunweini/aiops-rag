from pydantic import BaseModel, Field
from typing import Optional


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)


class SourceItem(BaseModel):
    title: str
    score: Optional[float] = None
    engine: str
    snippet: str = ""
    confidence: Optional[str] = None
    source_path: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem] = []
    degraded: bool = False
    missing_components: list[str] = []
    gap_warning: Optional[str] = None


class TopologyRequest(BaseModel):
    service_id: str


class TopologyResponse(BaseModel):
    service_id: str
    service_name: str = ""
    hosts: list[dict] = []
    ports: list[int] = []
    calls: list[dict] = []
    called_by: list[dict] = []


class IndexRequest(BaseModel):
    file_path: str
    content: str = ""


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
