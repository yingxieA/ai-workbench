"""配置加载"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 数据库
    DATABASE_URL: str = "postgresql://workbench:workbench123@localhost:5433/workbench"
    REDIS_URL: str = "redis://localhost:6380/0"

    # LLM
    DASHSCOPE_API_KEY: str = ""
    LLM_MODEL: str = "qwen-plus"
    DEEPSEEK_API_KEY: str = ""  # DeepSeek 兜底模型 key（用户后续自行配置）

    # LangSmith 链路追踪
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "ai-workbench-agent"

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

    # P2.3 前置过滤层
    PREFILTER_RATE_LIMIT: int = 20  # 用户维度：时间窗内最大提问次数
    PREFILTER_RATE_WINDOW: int = 60  # 秒
    PREFILTER_MAX_LEN: int = 2000  # 问题最大长度（超出提示精简）
    PREFILTER_LIGHT_ENABLED: bool = False  # L2 轻量模型闲聊分类（默认关；开启需 DASHSCOPE key）
    PREFILTER_LIGHT_MODEL: str = "qwen-turbo"

    # 安全护栏（安全治理）：Prompt 注入防护 + 输出内容审核
    GUARDRAIL_INJECTION_ENABLED: bool = True  # 输入侧注入检测（可被 system_configs 覆盖）
    GUARDRAIL_OUTPUT_ENABLED: bool = True  # 输出侧内容审核（可被 system_configs 覆盖）

    class Config:
        env_file = ".env"


settings = Settings()
