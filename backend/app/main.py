"""FastAPI 入口"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.api import documents, chat, news, skills, learning, auth, review, recommend
from app.services.news import generate_daily_news
from app.database import engine, SessionLocal
from app.models.user import User
from app.api.auth import hash_password
from app.utils.logger import get_logger
import uuid

logger = get_logger("main")


scheduler = AsyncIOScheduler()


def init_default_user():
    db = SessionLocal()
    if not db.query(User).first():
        user = User(
            id=str(uuid.uuid4()),
            username="admin",
            password_hash=hash_password("admin123"),
            nickname="管理员"
        )
        db.add(user)
        db.commit()
    db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import Base
    Base.metadata.create_all(bind=engine)
    init_default_user()
    # 预热 embedding 模型
    from app.services.embedding import get_embedding_model
    logger.info("预热 embedding 模型...")
    get_embedding_model()
    logger.info("embedding 模型预热完成")
    scheduler.add_job(generate_daily_news, "cron", hour=8, minute=0, id="daily_news")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="AI Workbench", version="0.1.0", lifespan=lifespan)

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
app.include_router(chat.router)
app.include_router(news.router)
app.include_router(skills.router)
app.include_router(learning.router)
app.include_router(auth.router)
app.include_router(review.router)
app.include_router(recommend.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "ai-workbench"}
