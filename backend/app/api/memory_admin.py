"""记忆管理接口（仅管理员）：会话摘要 / 用户画像的查询、修正、删除 + 审计留痕

- 会话记忆：session_summaries（摘要）+ Redis 热缓存（chat:history:*）双清理
- 长期记忆：user_profiles（JSONB 画像）
- 所有写操作写 AuditLog（action: edit_summary / delete_session_memory / edit_profile / delete_profile）
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.document import AuditLog, ChatSession, ChatMessage, SessionSummary, UserProfile
from app.api.auth import require_admin
from app.services.memory_service import cache_invalidate
from app.utils.logger import get_logger

logger = get_logger("memory_admin")
router = APIRouter(prefix="/api/memories", tags=["memory-admin"])


# ---------- 会话记忆 ----------


@router.get("/sessions")
def list_session_memories(
    skip: int = 0,
    limit: int = 30,
    username: str = "",
    has_summary: bool = False,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """会话记忆列表：所有会话（LEFT JOIN 摘要，无摘要显示"无摘要"）+ 用户（可按用户名过滤 / 只看有摘要）"""
    q = (
        db.query(ChatSession, User, SessionSummary)
        .outerjoin(User, User.id == ChatSession.user_id)
        .outerjoin(SessionSummary, SessionSummary.session_id == ChatSession.id)
        .filter(ChatSession.is_deleted.is_(False))
    )
    if has_summary:
        q = q.filter(
            SessionSummary.session_id.isnot(None), SessionSummary.summary != "", SessionSummary.summary.isnot(None)
        )
    if username:
        q = q.filter(User.username.ilike(f"%{username}%"))

    total = q.count()
    # 有摘要的会话按摘要更新时间倒序，无摘要的排最后（按会话更新时间）
    rows = (
        q.order_by(
            SessionSummary.updated_at.desc().nullslast(),
            ChatSession.updated_at.desc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )

    # 批量取各会话消息数（避免 N+1）
    sids = [r[0].id for r in rows]
    counts = {}
    if sids:
        for sid, c in (
            db.query(ChatMessage.session_id, ChatMessage.id)
            .filter(ChatMessage.session_id.in_(sids), ChatMessage.is_deleted.is_(False))
            .all()
        ):
            counts[str(sid)] = counts.get(str(sid), 0) + 1

    return {
        "total": total,
        "items": [
            {
                "session_id": str(sess.id),
                "title": sess.title,
                "user_id": sess.user_id,
                "username": u.username if u else "未知",
                "summary": s.summary if s else "",
                "version": s.version if s else 0,
                "updated_at": s.updated_at.isoformat() if s and s.updated_at else None,
                "created_at": sess.created_at.isoformat() if sess.created_at else None,
                "msg_count": counts.get(str(sess.id), 0),
            }
            for sess, u, s in rows
        ],
    }


@router.get("/sessions/{session_id}")
def get_session_memory_detail(
    session_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """会话记忆详情：摘要 + 最近 6 条消息（辅助判断摘要准确性）"""
    try:
        sid = uuid.UUID(session_id)
    except Exception:
        raise HTTPException(status_code=400, detail="session_id 格式错误")

    sum_row = db.query(SessionSummary).filter(SessionSummary.session_id == sid).first()
    recent = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == sid, ChatMessage.is_deleted.is_(False))
        .order_by(ChatMessage.created_at.desc())
        .limit(6)
        .all()
    )
    return {
        "summary": sum_row.summary if sum_row else "",
        "version": sum_row.version if sum_row else 0,
        "updated_at": sum_row.updated_at.isoformat() if sum_row and sum_row.updated_at else None,
        "recent_messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in reversed(recent)
        ],
    }


@router.put("/sessions/{session_id}/summary")
def edit_session_summary(
    session_id: str,
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """人工修正会话摘要（version+1，写审计）"""
    summary = (payload.get("summary") or "").strip()
    if not summary:
        raise HTTPException(status_code=400, detail="摘要不能为空")
    try:
        sid = uuid.UUID(session_id)
    except Exception:
        raise HTTPException(status_code=400, detail="session_id 格式错误")

    row = db.query(SessionSummary).filter(SessionSummary.session_id == sid).first()
    if row is None:
        row = SessionSummary(session_id=sid, summary=summary, version=0)
        db.add(row)
    else:
        row.summary = summary
        row.version = (row.version or 0) + 1
    db.add(
        AuditLog(
            user_id=admin.id,
            action="edit_summary",
            target_id=session_id,
            detail={"version": row.version, "preview": summary[:120]},
        )
    )
    db.commit()
    return {"ok": True, "version": row.version}


@router.delete("/sessions/{session_id}/memory")
def delete_session_memory(
    session_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """删除会话记忆：摘要行 + Redis 热缓存双清（聊天消息保留可审计）"""
    try:
        sid = uuid.UUID(session_id)
    except Exception:
        raise HTTPException(status_code=400, detail="session_id 格式错误")

    deleted = db.query(SessionSummary).filter(SessionSummary.session_id == sid).delete()
    cache_invalidate(session_id)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="delete_session_memory",
            target_id=session_id,
            detail={"deleted_summary": deleted},
        )
    )
    db.commit()
    return {"ok": True, "deleted_summary": deleted}


# ---------- 用户画像 ----------


@router.get("/profiles")
def list_user_profiles(
    skip: int = 0,
    limit: int = 30,
    username: str = "",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """用户画像列表：画像 JSONB + 用户（可按用户名过滤）"""
    q = db.query(UserProfile, User).outerjoin(User, User.id == UserProfile.user_id)
    if username:
        q = q.filter(User.username.ilike(f"%{username}%"))

    total = q.count()
    rows = q.order_by(UserProfile.updated_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "user_id": p.user_id,
                "username": u.username if u else "未知",
                "profile": p.profile_json or {},
                "version": p.version,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p, u in rows
        ],
    }


@router.get("/profiles/{user_id}")
def get_user_profile_detail(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """用户画像详情"""
    row = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if row is None:
        return {"user_id": user_id, "profile": {}, "version": 0, "updated_at": None}
    return {
        "user_id": row.user_id,
        "profile": row.profile_json or {},
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/profiles/{user_id}")
def edit_user_profile(
    user_id: str,
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """人工修正画像（整体替换，version+1，写审计）"""
    profile = payload.get("profile")
    if not isinstance(profile, dict) or not profile:
        raise HTTPException(status_code=400, detail="profile 必须是非空 JSON 对象")

    row = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if row is None:
        row = UserProfile(user_id=user_id, profile_json=profile, version=0)
        db.add(row)
    else:
        row.profile_json = profile
        row.version = (row.version or 0) + 1
    db.add(
        AuditLog(
            user_id=admin.id,
            action="edit_profile",
            target_id=user_id,
            detail={"version": row.version, "keys": list(profile.keys())},
        )
    )
    db.commit()
    return {"ok": True, "version": row.version}


@router.delete("/profiles/{user_id}")
def delete_user_profile(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """清空用户画像（删除行，写审计）"""
    deleted = db.query(UserProfile).filter(UserProfile.user_id == user_id).delete()
    db.add(
        AuditLog(
            user_id=admin.id,
            action="delete_profile",
            target_id=user_id,
            detail={"deleted": deleted},
        )
    )
    db.commit()
    return {"ok": True, "deleted": deleted}
