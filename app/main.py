"""FastAPI app entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.monitor import monitor_middleware, get_metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.retrievers.es_retriever import get_es_client, init_index
    from app.retrievers.graph_retriever import get_driver, init_schema

    es = get_es_client()
    init_index(es)

    driver = get_driver()
    init_schema(driver)

    yield

    from app.retrievers.graph_retriever import close_driver
    from app.retrievers.es_retriever import close_es
    close_driver()
    close_es()


app = FastAPI(
    title="AIOps RAG API",
    description="AIOps knowledge base with ES + Neo4j + Vector + Rerank",
    version="0.1.0",
    lifespan=lifespan,
)

app.middleware("http")(monitor_middleware)
app.include_router(router, prefix="/api/v1")


@app.get("/api/v1/metrics")
def metrics():
    return get_metrics()
