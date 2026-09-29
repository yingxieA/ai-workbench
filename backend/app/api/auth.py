"""认证接口：注册 / 登录 / 刷新 / 登出 / 改密"""

import uuid
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt
import bcrypt

from app.database import get_db
from app.models.user import User, RefreshToken
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

SECRET_KEY = "ai-workbench-secret-key-change-in-prod"
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 15  # access token 短时效
REFRESH_TOKEN_DAYS = 7  # refresh token 7 天
MAX_LOGIN_FAILS = 5  # 连续失败锁定阈值
LOCK_MINUTES = 15  # 锁定时长
RATE_LIMIT_WINDOW = 60  # 秒
RATE_LIMIT_MAX = 5  # 窗口内最大尝试

# Redis 客户端（容错：Redis 不可用时降级为内存限流）
try:
    import redis as redis_lib

    _redis = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis.ping()
    REDIS_OK = True
except Exception as e:
    logger.warning(f"Redis 不可用，限流降级为内存模式: {e}")
    _redis = None
    REDIS_OK = False

_mem_attempts = {}  # 内存限流兜底


def hash_password(pwd: str) -> str:
    """bcrypt 加盐哈希"""
    return bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pwd: str, stored_hash: str) -> bool:
    """双轨校验：bcrypt（新）+ SHA-256 无盐（旧，兼容存量 admin）"""
    if stored_hash.startswith("$2"):  # bcrypt 格式
        try:
            return bcrypt.checkpw(pwd.encode("utf-8"), stored_hash.encode("utf-8"))
        except ValueError:
            return False
    # 旧格式：SHA-256 hex（无盐）
    return hashlib.sha256(pwd.encode("utf-8")).hexdigest() == stored_hash


def is_legacy_hash(stored_hash: str) -> bool:
    """是否为旧 SHA-256 哈希（登录成功后需升级为 bcrypt）"""
    return not stored_hash.startswith("$2")


def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: str) -> tuple[str, str]:
    """生成 refresh token，返回 (明文token, sha256哈希)"""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, token_hash


def _rate_limit_key(kind: str, ip: str, username: str) -> str:
    return f"auth:{kind}:{ip}:{username}"


def check_rate_limit(ip: str, username: str) -> bool:
    """登录限流：窗口内超过阈值返回 False"""
    key = _rate_limit_key("login", ip, username)
    if REDIS_OK:
        try:
            count = _redis.incr(key)
            if count == 1:
                _redis.expire(key, RATE_LIMIT_WINDOW)
            return count <= RATE_LIMIT_MAX
        except Exception:
            pass
    # 内存兜底
    now = time.time()
    entry = _mem_attempts.get(key, [])
    entry = [t for t in entry if now - t < RATE_LIMIT_WINDOW]
    if len(entry) >= RATE_LIMIT_MAX:
        return False
    entry.append(now)
    _mem_attempts[key] = entry
    return True


def reset_rate_limit(ip: str, username: str):
    key = _rate_limit_key("login", ip, username)
    if REDIS_OK:
        try:
            _redis.delete(key)
        except Exception:
            pass
    _mem_attempts.pop(key, None)


def _log_login(
    db: Session,
    user_id,
    username,
    success: bool,
    ip: str,
    user_agent: str,
    reason: str = "",
):
    """写登录审计"""
    try:
        from app.models.user import LoginLog

        db.add(
            LoginLog(
                user_id=user_id,
                username=username,
                success=success,
                ip=ip,
                user_agent=user_agent[:300],
                reason=reason,
            )
        )
        db.commit()
    except Exception as e:
        logger.warning(f"写审计日志失败: {e}")
        db.rollback()


class RegisterRequest(BaseModel):
    username: str
    password: str
    nickname: str = ""
    email: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册"""
    username = req.username.strip()
    password = req.password
    email = (req.email or "").strip().lower()

    # 校验
    if len(username) < 3 or len(username) > 20:
        raise HTTPException(status_code=400, detail="用户名需 3-20 个字符")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise HTTPException(status_code=400, detail="密码需同时包含字母和数字")

    if db.query(User).filter(User.username == username, User.is_deleted == False).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if email and db.query(User).filter(User.email == email, User.is_deleted == False).first():
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        nickname=req.nickname.strip() or username,
        email=email or None,
        role_level=10,  # 新注册默认普通用户
    )
    db.add(user)
    db.commit()
    logger.info(f"新用户注册: {username}")

    # 注册即登录
    access_token = create_access_token(user.id)
    refresh_token, refresh_hash = create_refresh_token(user.id)
    db.add(_refresh_row(user.id, refresh_hash))
    db.commit()
    return {
        "token": access_token,
        "refresh_token": refresh_token,
        "user": _user_payload(user),
    }


def _refresh_row(user_id: str, token_hash: str):
    from app.models.user import RefreshToken

    return RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS),
    )


def _user_payload(user: User) -> dict:
    """统一用户返回结构（登录/注册/me 共用，带权限等级）"""
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname or user.username,
        "email": user.email,
        "role_level": user.role_level or 10,
        "is_admin": (user.role_level or 10) >= 100,
    }


def get_current_user(authorization: str = Header(""), db: Session = Depends(get_db)) -> User:
    """FastAPI 依赖：解析 JWT → 返回完整 User 对象（含 role_level）"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期")
    except Exception:
        raise HTTPException(status_code=401, detail="无效 token")
    user = db.query(User).filter(User.id == payload["sub"], User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


def require_level(min_level: int = 100):
    """权限依赖工厂：当前用户 role_level >= min_level 才放行，否则 403"""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if (user.role_level or 10) < min_level:
            raise HTTPException(status_code=403, detail="权限不足：需要更高权限等级")
        return user

    return _dep


require_admin = require_level(100)  # 管理接口专用依赖


@router.post("/login")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """登录：限流 → 锁定检查 → 密码校验（双轨）→ 签发双 token"""
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip, req.username):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请 1 分钟后再试")

    user = db.query(User).filter(User.username == req.username, User.is_deleted == False).first()
    if not user:
        _log_login(db, None, req.username, False, ip, "", "用户不存在")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 锁定检查
    now = datetime.now(timezone.utc)
    if user.locked_until and user.locked_until > now:
        _log_login(db, user.id, user.username, False, ip, "", "账号已锁定")
        raise HTTPException(status_code=423, detail="账号已锁定，请稍后再试")

    if not verify_password(req.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= MAX_LOGIN_FAILS:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_login_count = 0
            db.commit()
            _log_login(db, user.id, user.username, False, ip, "", "连续失败已锁定")
            raise HTTPException(
                status_code=423,
                detail=f"连续失败 {MAX_LOGIN_FAILS} 次，账号锁定 {LOCK_MINUTES} 分钟",
            )
        db.commit()
        _log_login(db, user.id, user.username, False, ip, "", "密码错误")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 成功：重置失败计数 + 升级旧哈希 + 记录登录时间
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    if is_legacy_hash(user.password_hash):
        user.password_hash = hash_password(req.password)  # 自动升级 bcrypt
    db.commit()
    reset_rate_limit(ip, req.username)
    _log_login(db, user.id, user.username, True, ip, "", "登录成功")

    access_token = create_access_token(user.id)
    refresh_token, refresh_hash = create_refresh_token(user.id)
    db.add(_refresh_row(user.id, refresh_hash))
    db.commit()
    logger.info(f"登录成功: {req.username}")
    return {
        "token": access_token,
        "refresh_token": refresh_token,
        "user": _user_payload(user),
    }


@router.post("/refresh")
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    """刷新 token：校验 refresh → 撤销旧 → 签发新双 token（轮换）"""
    token_hash = hashlib.sha256(req.refresh_token.encode("utf-8")).hexdigest()
    row = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=401, detail="refresh token 无效或已过期")

    user = db.query(User).filter(User.id == row.user_id, User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    # 轮换：撤销旧 refresh
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()

    access_token = create_access_token(user.id)
    new_refresh, new_hash = create_refresh_token(user.id)
    db.add(_refresh_row(user.id, new_hash))
    db.commit()
    return {"token": access_token, "refresh_token": new_refresh}


@router.post("/logout")
def logout(req: RefreshRequest, db: Session = Depends(get_db)):
    """登出：撤销 refresh token"""
    token_hash = hashlib.sha256(req.refresh_token.encode("utf-8")).hexdigest()
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    authorization: str = Header(""),
    db: Session = Depends(get_db),
):
    """修改密码：校验旧密码 → 更新 → 撤销该用户全部 refresh（强制重新登录）"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail="无效 token")

    user = db.query(User).filter(User.id == payload["sub"], User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="旧密码错误")
    if len(req.new_password) < 8 or not (
        any(c.isalpha() for c in req.new_password) and any(c.isdigit() for c in req.new_password)
    ):
        raise HTTPException(status_code=400, detail="新密码至少 8 位且包含字母和数字")

    user.password_hash = hash_password(req.new_password)
    # 撤销所有 refresh token
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).update(
        {RefreshToken.revoked_at: datetime.now(timezone.utc)}
    )
    db.commit()
    return {"ok": True}


def get_current_user_id(authorization: str = Header("")) -> str:
    """FastAPI 依赖：从 Authorization header 解析当前用户 id"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期")
    except Exception:
        raise HTTPException(status_code=401, detail="无效 token")


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """当前用户信息（含权限等级，前端据此控制界面）"""
    return _user_payload(user)
