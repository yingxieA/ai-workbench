# -*- coding: utf-8 -*-
"""系统配置管理 API（仅 admin）：热更新，无需重启"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import require_admin
from app.models.user import User
from app.models.document import AuditLog
from app.services.system_configs import list_configs, set_config
from app.utils.logger import get_logger

logger = get_logger("system_configs")
router = APIRouter(prefix="/api/system-configs", tags=["system-configs"])


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str
    description: str = ""


def _audit(db: Session, user: User, action: str, detail: dict):
    try:
        db.add(AuditLog(user_id=user.id, action=action, detail=detail))
    except Exception as e:
        logger.warning(f"写审计日志失败: {e}")


@router.get("")
def get_all_configs(admin: User = Depends(require_admin)):
    return {"items": list_configs()}


@router.put("")
def update_config(req: ConfigUpdateRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """更新配置（热更新立即生效）"""
    req.key = (req.key or "").strip()
    if not req.key:
        raise HTTPException(status_code=400, detail="key 不能为空")
    if req.value is None or str(req.value).strip() == "":
        raise HTTPException(status_code=400, detail="value 不能为空")
    result = set_config(req.key, str(req.value), req.description, admin.username)
    _audit(db, admin, "update_config", {"key": req.key, "value": req.value, "action": result["action"]})
    db.commit()
    return result
