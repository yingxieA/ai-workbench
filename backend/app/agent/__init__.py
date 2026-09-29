"""智能问答 Agent 包：LangGraph 图 + LlamaIndex 检索 + 模型降级链"""

import os
from app.config import settings

# LangSmith 全链路追踪（进程内全局生效）
if settings.LANGSMITH_API_KEY:
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
