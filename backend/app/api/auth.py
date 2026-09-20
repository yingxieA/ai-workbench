"""认证接口"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import hashlib
import jwt

from app.database import get_db
from app.models.user import User
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

SECRET_KEY = "ai-workbench-secret-key-change-in-prod"
ALGORITHM = "HS256"
EXPIRE_HOURS = 24


def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(hours=EXPIRE_HOURS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    logger.info(f"登录尝试: {req.username}")
    user = db.query(User).filter(User.username == req.username).first()
    if not user or user.password_hash != hash_password(req.password):
        logger.warning(f"登录失败: {req.username}")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_token(user.id)
    logger.info(f"登录成功: {req.username}")
    return {
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname or user.username
        }
    }


@router.get("/me")
def me(authorization: str = "", db: Session = Depends(get_db)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user = db.query(User).filter(User.id == payload["sub"]).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        return {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname or user.username
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期")
    except Exception:
        raise HTTPException(status_code=401, detail="无效 token")
