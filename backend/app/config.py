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

    # 认证（P0：SECRET_KEY 生产必须通过环境变量/.env 注入，缺省仅用于本地开发）
    SECRET_KEY: str = ""
    JWT_ISSUER: str = "ai-workbench"
    JWT_AUDIENCE: str = "ai-workbench-api"

    # Apify
    APIFY_API_KEY: str = ""

    # P2.3 前置过滤层
    PREFILTER_RATE_LIMIT: int = 20  # 用户维度：时间窗内最大提问次数
    PREFILTER_RATE_WINDOW: int = 60  # 秒
    PREFILTER_MAX_LEN: int = 2000  # 问题最大长度（超出提示精简）
    PREFILTER_DUP_WINDOW: int = (
        300  # 内容重复判定时间窗口（秒）：窗口内相同提问才算 spam，窗口外重问放行（防"永远不能重问"）
    )
    PREFILTER_LIGHT_ENABLED: bool = False  # L2 轻量模型闲聊分类（默认关；开启需 DASHSCOPE key）
    PREFILTER_LIGHT_MODEL: str = "qwen-turbo"
    PREFILTER_INJECTION_LIGHT_ENABLED: bool = (
        True  # P1：注入检测轻量模型二次判定（仅含弱注入特征才触发，条件触发成本低）
    )
    PREFILTER_INJECTION_LIGHT_MODEL: str = "qwen-turbo"
    PREFILTER_OUTPUT_AUDIT_STREAMING: bool = True  # P0：输出审核流式分段（命中即中断，默认开）
    PREFILTER_OUTPUT_AUDIT_CHUNK: int = 200  # P0：每累积多少字符审一次（越小拦截越快，成本略增）
    PREFILTER_AC_ENABLED: bool = True  # 敏感词/违规词检测用 AC 自动机（pyahocorasick）；库缺失自动回退正则 union

    # 安全护栏（安全治理）：Prompt 注入防护 + 输出内容审核
    GUARDRAIL_INJECTION_ENABLED: bool = True  # 输入侧注入检测（可被 system_configs 覆盖）
    GUARDRAIL_OUTPUT_ENABLED: bool = True  # 输出侧内容审核（可被 system_configs 覆盖）

    class Config:
        env_file = ".env"


settings = Settings()
