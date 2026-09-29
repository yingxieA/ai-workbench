"""用户管理接口（仅管理员）：用户列表 / 角色等级 / 启用禁用"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.document import AuditLog
from app.api.auth import require_admin
from app.utils.logger import get_logger

logger = get_logger("users")
router = APIRouter(prefix="/api/users", tags=["users"])

ALLOWED_LEVELS = {10, 50, 100}
ALLOWED_STATUS = {"active", "disabled"}


class RoleLevelRequest(BaseModel):
    role_level: int


class StatusRequest(BaseModel):
    status: str


def _audit(db: Session, user_id: str, action: str, target_id: str, detail: dict):
    db.add(AuditLog(user_id=user_id, action=action, target_id=target_id, detail=detail))
    db.commit()


@router.get("")
def list_users(
    skip: int = 0,
    limit: int = 20,
    search: str = "",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """用户列表（admin）：分页 + 用户名/昵称/邮箱模糊搜索 + 统计"""
    q = db.query(User).filter(User.is_deleted == False)
    if search.strip():
        kw = f"%{search.strip()}%"
        q = q.filter((User.username.ilike(kw)) | (User.nickname.ilike(kw)) | (User.email.ilike(kw)))
    total = q.count()

    # 统计（全量，不受分页影响）：等级分布 + 状态分布
    def _cnt(**conds):
        sub = db.query(User).filter(User.is_deleted == False)
        for k, v in conds.items():
            if v is not None:
                sub = sub.filter(getattr(User, k) == v)
        return sub.count()

    stats = {
        "total": total,
        "admin": _cnt(role_level=100),
        "senior": _cnt(role_level=50),
        "normal": _cnt(role_level=10),
        "disabled": _cnt(status="disabled"),
    }

    users = q.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "stats": stats,
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "nickname": u.nickname or u.username,
                "email": u.email,
                "role_level": u.role_level or 10,
                "status": u.status or "active",
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
    }


@router.patch("/{user_id}/role_level")
def set_role_level(
    user_id: str,
    req: RoleLevelRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """修改用户角色等级（admin）：10 普通 / 50 高级 / 100 管理员"""
    if req.role_level not in ALLOWED_LEVELS:
        raise HTTPException(status_code=400, detail="等级仅支持 10 / 50 / 100")
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能修改自己的权限等级（防止唯一超管被降权）")
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    old_level = user.role_level or 10
    user.role_level = req.role_level
    db.commit()
    _audit(
        db,
        admin.id,
        "set_role",
        user.id,
        {"username": user.username, "old_level": old_level, "new_level": req.role_level},
    )
    logger.info(f"admin {admin.username} 将 {user.username} 等级 {old_level} -> {req.role_level}")
    return {"id": user.id, "role_level": user.role_level}


@router.patch("/{user_id}/status")
def set_user_status(
    user_id: str,
    req: StatusRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """启用 / 禁用用户（admin）：禁用后该用户无法登录与调用接口"""
    if req.status not in ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="状态仅支持 active / disabled")
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    old_status = user.status or "active"
    user.status = req.status
    db.commit()
    _audit(
        db,
        admin.id,
        "set_status",
        user.id,
        {"username": user.username, "old_status": old_status, "new_status": req.status},
    )
    logger.info(f"admin {admin.username} 将 {user.username} 状态 {old_status} -> {req.status}")
    return {"id": user.id, "status": user.status}
