"""配置加载"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 数据库
    DATABASE_URL: str = "postgresql://workbench:workbench123@localhost:5433/workbench"
    REDIS_URL: str = "redis://localhost:6380/0"

    # LLM
    DASHSCOPE_API_KEY: str = ""
    LLM_MODEL: str = "qwen-plus"

    # Embedding
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_DEVICE: str = "cuda"
    HF_ENDPOINT: str = "https://hf-mirror.com"

    # Reranker
    RERANKER_MODEL: str = "bge-reranker-v2-m3"
    RERANKER_DEVICE: str = "cuda"

    # PDF
    MINERU_API_URL: str = "https://mineru.net/api/v4"
    MINERU_API_KEY: str = ""

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173"

    # Apify
    APIFY_API_KEY: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
