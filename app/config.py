from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    es_host: str = "elasticsearch"
    es_port: int = 9200
    es_user: str = "elastic"
    es_password: str = ""
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "rag-password"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = "https://api.deepseek.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_api_key: str = ""
    rerank_model: str = ""
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    log_level: str = "INFO"

    @property
    def es_url(self) -> str:
        auth = f"{self.es_user}:{self.es_password}@" if self.es_password else ""
        return f"http://{auth}{self.es_host}:{self.es_port}"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
