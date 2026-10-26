"""FastAPI 入口"""

import asyncio
import os as _os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from app.core.errors import BizError, ErrorCode, error_payload, extract_error_code
from app.core.metrics import record_error
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.api import (
    documents,
    chat,
    chat_sessions,
    news,
    skills,
    learning,
    auth,
    review,
    recommend,
    travel,
    rules,
    users,
    audit,
    tools,
    intent_rules,
    system_configs,
    memory_admin,
    feedbacks,
    debug,
    eval_admin,
)
from app.services.news import generate_daily_news
from app.agent.cancels import cleanup_epoch_job
from app.database import engine, SessionLocal
from app.models.user import User
from app.api.auth import hash_password
from app.utils.logger import get_logger
import uuid

logger = get_logger("main")

# LangSmith 链路追踪：SDK 只读 os.environ（pydantic settings 不会自动注入环境变量），
# 必须在任何 @traceable 调用前把 key / 开关 / 项目名写入环境变量，否则追踪静默失效
if settings.LANGSMITH_API_KEY:
    _os.environ.setdefault("LANGSMITH_API_KEY", settings.LANGSMITH_API_KEY)
    _os.environ.setdefault("LANGSMITH_TRACING", "true")
    _os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")  # langchain 生态的追踪开关（兼容旧版 SDK）
    _os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT or "ai-workbench-agent")
    _os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT or "ai-workbench-agent")
    logger.info(f"LangSmith 链路追踪已启用: project={settings.LANGSMITH_PROJECT or 'ai-workbench-agent'}")
else:
    logger.warning("LANGSMITH_API_KEY 未配置，链路追踪不生效（不影响业务）")


scheduler = AsyncIOScheduler()


def init_default_user():
    db = SessionLocal()
    if not db.query(User).first():
        user = User(
            id=str(uuid.uuid4()),
            username="admin",
            password_hash=hash_password("admin123"),
            nickname="管理员",
        )
        db.add(user)
        db.commit()
    db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import Base
    from app.core.pool import bg_pool, shutdown_bg_pool

    Base.metadata.create_all(bind=engine)
    init_default_user()
    # 应用级后台任务线程池：启动即创建（标题精修/幻觉检测/PDF解析/测评等待统一在此执行）
    bg_pool()
    # 预热 embedding 模型
    from app.services.embedding import get_embedding_model

    logger.info("预热 embedding 模型...")
    get_embedding_model()
    logger.info("embedding 模型预热完成")
    # 工具池状态与 DB 同步（启停状态恢复 + 外部 MCP 自动重连）
    try:
        from app.api.tools import sync_tools_from_db

        # 同步函数内部用 asyncio.new_event_loop() 连接外部 MCP，
        # 必须放到线程池执行，避免与 lifespan 的 running loop 冲突
        await asyncio.to_thread(sync_tools_from_db)
    except Exception as e:
        logger.error(f"工具池状态同步失败: {e}")
    # 错过窗口（misfire_grace_time）内补跑 + 合并积压（coalesce），防休眠/重启导致漏报/漏清理
    scheduler.add_job(
        generate_daily_news,
        "cron",
        hour=8,
        minute=0,
        id="daily_news",
        misfire_grace_time=3 * 3600,
        coalesce=True,
    )
    # P2：每 10 分钟清理会话代际残留（MemorySaver checkpoint + session_epochs 水位），防高频打断内存泄漏
    scheduler.add_job(
        cleanup_epoch_job,
        "interval",
        minutes=10,
        id="epoch_cleanup",
        misfire_grace_time=1800,
        coalesce=True,
    )
    # 测评中心定时调度（配置存 system_configs，开关默认关闭，人工开启才排程）
    eval_admin.setup_eval_job(scheduler)
    scheduler.start()
    yield
    scheduler.shutdown()
    # 应用级后台任务线程池：优雅等待已提交任务收尾
    shutdown_bg_pool()


app = FastAPI(title="AI Workbench", version="0.1.0", lifespan=lifespan)


# ── 统一错误响应（P0：所有 REST 端点自动输出 {error_code, message, request_id}）──
@app.exception_handler(BizError)
async def _biz_error_handler(request: Request, exc: BizError):
    record_error(exc.error_code)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.error_code, exc.message, detail=exc.detail),
    )


@app.exception_handler(HTTPException)
async def _http_error_handler(request: Request, exc: HTTPException):
    # detail 兼容：新写法 (ErrorCode, 中文) → 精确码；旧写法纯中文 → 按状态码兜底码
    if isinstance(exc.detail, tuple) and len(exc.detail) == 2:
        code, msg = exc.detail
        if not isinstance(code, (str, ErrorCode)):
            code, msg = extract_error_code(exc.detail, exc.status_code), str(exc.detail)
    else:
        code, msg = extract_error_code(exc.detail, exc.status_code), str(exc.detail)
    record_error(code)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(code, msg),
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception):
    # 兜底：未捕获异常 → 500 统一格式（日志留痕，不泄漏内部细节）
    import logging

    logging.getLogger("main").exception("未捕获异常: %s", exc)
    record_error(ErrorCode.INTERNAL_ERROR)
    return JSONResponse(
        status_code=500,
        content=error_payload(ErrorCode.INTERNAL_ERROR, "服务内部错误，请稍后再试"),
    )


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(documents.router)
app.include_router(debug.router)
app.include_router(chat.router)
app.include_router(chat_sessions.router)
app.include_router(news.router)
app.include_router(skills.router)
app.include_router(learning.router)
app.include_router(auth.router)
app.include_router(review.router)
app.include_router(recommend.router)
app.include_router(travel.router)
app.include_router(rules.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(tools.router)
app.include_router(intent_rules.router)
app.include_router(system_configs.router)
app.include_router(memory_admin.router)
app.include_router(feedbacks.router)
app.include_router(eval_admin.router)

# MCP Server：把工具池暴露为 SSE 传输的标准 MCP 端点（外部系统可通过 MCP 协议调用本工作台工具）
try:
    from app.agent.tools.mcp_server import router as mcp_router

    app.include_router(mcp_router)
    logger.info("MCP Server 已挂载: /mcp (SSE 传输)")
except Exception as e:
    logger.error(f"MCP Server 挂载失败: {e}")


@app.get("/health")
def health():
    return {"status": "ok", "service": "ai-workbench"}
