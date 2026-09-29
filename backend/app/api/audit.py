"""审计日志查询接口（仅管理员）：全操作留痕，谁在何时做了什么"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.document import AuditLog
from app.api.auth import require_admin
from app.utils.logger import get_logger

logger = get_logger("audit")
router = APIRouter(prefix="/api/audit-logs", tags=["audit"])

# 允许过滤的操作类型（前端下拉同源，与各接口实际写入值保持一致）
ACTIONS = [
    "upload_document",
    "delete_document",
    "rename_document",
    "set_permission",
    "review_chunk",
    "relabel_document",
    "add_keyword",
    "delete_keyword",
    "set_role",
    "set_status",
    # P2.5 记忆管理
    "edit_summary",
    "delete_session_memory",
    "edit_profile",
    "delete_profile",
]


@router.get("")
def list_audit_logs(
    skip: int = 0,
    limit: int = 30,
    action: str = "",
    user_id: str = "",
    start: str = "",
    end: str = "",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """审计日志列表（admin）：分页 + 按操作类型 / 操作人 / 时间范围过滤"""
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)

    def _parse(s: str):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            raise HTTPException(status_code=400, detail=f"时间格式应为 YYYY-MM-DD: {s}")

    if start:
        q = q.filter(AuditLog.created_at >= _parse(start))
    if end:
        # 含结束日整天
        end_dt = _parse(end)
        q = q.filter(AuditLog.created_at < end_dt.replace(hour=23, minute=59, second=59))

    total = q.count()
    logs = q.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()

    # 批量取操作人用户名（避免 N+1）
    uids = {log.user_id for log in logs if log.user_id}
    users = db.query(User).filter(User.id.in_(uids)).all() if uids else []
    username_map = {u.id: u.username for u in users}

    return {
        "total": total,
        "actions": ACTIONS,
        "items": [
            {
                "id": str(log.id),
                "user_id": log.user_id,
                "username": username_map.get(log.user_id, "未知"),
                "action": log.action,
                "target_id": log.target_id,
                "detail": log.detail or {},
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
