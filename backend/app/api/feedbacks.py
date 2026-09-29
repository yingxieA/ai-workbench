# -*- coding: utf-8 -*-
"""反馈管理 API（P2.6 在线反馈回流，仅 admin）
- 列表：全部点赞/点踩 + 关联路由/模型/时间，按类型/路由/复核状态过滤
- 复核：approved（入训练集）/ rejected（弃用）/ pending（待定）
- 导出：复核通过的标注数据 → JSONL 训练集（供微调/评测）
"""

import json
import io
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.document import ChatMessage, ChatSession, ChatRouterLog, AuditLog
from app.api.auth import require_admin
from app.utils.logger import get_logger

logger = get_logger("feedbacks")
router = APIRouter(prefix="/api/feedbacks", tags=["feedbacks"])

REVIEW_STATUSES = ["pending", "approved", "rejected"]


def _audit(db: Session, user: User, action: str, detail: dict):
    try:
        db.add(AuditLog(user_id=user.id, action=action, detail=detail))
    except Exception as e:
        logger.warning(f"写审计日志失败: {e}")


@router.get("")
def list_feedbacks(
    skip: int = 0,
    limit: int = 30,
    fb_type: str = "",  # like / dislike / 空=全部
    route: str = "",  # direct / rag / tool / agent_loop
    review_status: str = "",  # pending / approved / rejected / 空=全部
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """反馈列表：消息 + 会话标题 + 路由 + 模型 + 复核状态（只查有反馈的记录）"""
    q = (
        db.query(ChatMessage, ChatSession, ChatRouterLog)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .outerjoin(ChatRouterLog, ChatRouterLog.session_id == ChatMessage.session_id)
        .filter(ChatMessage.is_deleted.is_(False), ChatMessage.feedback.isnot(None))
    )
    if fb_type:
        q = q.filter(ChatMessage.feedback == fb_type)
    if review_status:
        q = q.filter(ChatMessage.review_status == review_status)
    if route:
        q = q.filter(ChatRouterLog.route_path == route)

    total = q.count()
    rows = q.order_by(ChatMessage.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "review_statuses": REVIEW_STATUSES,
        "items": [
            {
                "message_id": str(m.id),
                "session_id": str(s.id),
                "session_title": s.title,
                "user_id": s.user_id,
                "role": m.role,
                "content": m.content[:2000],
                "feedback": m.feedback,
                "feedback_reason": m.feedback_reason or "",
                "review_status": m.review_status or "pending",
                "route": r.route_path if r else "",
                "model_used": r.model_used if r else "",
                "latency_ms": r.latency_ms if r else None,
                "cost": r.cost if r else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, s, r in rows
        ],
    }


class ReviewRequest(BaseModel):
    status: str  # approved / rejected


@router.get("/hallucinations")
def list_hallucinations(
    skip: int = 0,
    limit: int = 30,
    verdict: str = "",  # supported / partially / unsupported / 空=全部
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """幻觉检测列表（LLM as a Judge 结果）：问题 + 回答 + 片段 + verdict + reason"""
    from app.models.document import ChatRouterLog

    q = db.query(ChatRouterLog).filter(ChatRouterLog.hallucination_verdict.isnot(None))
    if verdict:
        q = q.filter(ChatRouterLog.hallucination_verdict == verdict)
    total = q.count()
    rows = q.order_by(ChatRouterLog.created_at.desc()).offset(skip).limit(limit).all()

    # 批量取对应会话的回答（该次请求后的第一条 assistant 消息）
    from app.models.document import ChatMessage

    answers = {}
    for r in rows:
        if r.session_id is None:
            continue
        m = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.session_id == r.session_id,
                ChatMessage.role == "assistant",
                ChatMessage.is_deleted.is_(False),
            )
            .order_by(ChatMessage.created_at.desc())
            .first()
        )
        if m:
            answers[str(r.id)] = m.content

    return {
        "total": total,
        "items": [
            {
                "id": str(r.id),
                "session_id": str(r.session_id) if r.session_id else "",
                "question": r.question,
                "answer": answers.get(str(r.id), "")[:2000],
                "contexts": (r.contexts or "")[:2000],
                "verdict": r.hallucination_verdict,
                "score": r.hallucination_score,
                "reason": r.judge_reason or "",
                "route": r.route_path,
                "model_used": r.model_used,
                "latency_ms": r.latency_ms,
                "cost": r.cost,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/costs")
def cost_dashboard(
    days: int = 7,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """成本看板：今日成本 / 近 N 天逐日 / 按模型 / 按路由 / 当前配置（超限降级阈值）"""
    from sqlalchemy import text
    from app.services import cost_service
    from app.models.document import ChatRouterLog
    from datetime import datetime, timedelta, timezone

    BJ_TZ = timezone(timedelta(hours=8))
    today_start = datetime.now(BJ_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    # 今日成本
    today_cost = cost_service.daily_cost()

    # 近 N 天逐日（含今日）
    daily = db.execute(
        text("""
            SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS d,
                   COALESCE(SUM(cost), 0) AS cost,
                   COUNT(*) AS calls
            FROM chat_router_logs
            WHERE created_at >= :start
            GROUP BY d ORDER BY d
        """),
        {"start": today_start - timedelta(days=days - 1)},
    ).fetchall()
    daily_rows = [{"date": str(r[0]), "cost": float(r[1] or 0), "calls": r[2]} for r in daily]

    # 按模型汇总
    by_model = db.query(ChatRouterLog.model_used, ChatRouterLog.cost, ChatRouterLog.token_usage).all()
    model_map = {}
    for m, c, t in by_model:
        key = m or "unknown"
        e = model_map.setdefault(key, {"model": key, "cost": 0.0, "calls": 0, "tokens": 0})
        e["cost"] += c or 0
        e["calls"] += 1
        e["tokens"] += t or 0
    model_rows = [
        {"model": k, **{kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items() if kk != "model"}}
        for k, v in sorted(model_map.items(), key=lambda x: -x[1]["cost"])
    ]

    # 按路由汇总
    route_map = {}
    for r in db.query(ChatRouterLog.route_path, ChatRouterLog.cost).all():
        key = r.route_path or "unknown"
        e = route_map.setdefault(key, {"route": key, "cost": 0.0, "calls": 0})
        e["cost"] += r.cost or 0
        e["calls"] += 1
    route_rows = [
        {"route": k, "cost": round(v["cost"], 4), "calls": v["calls"]}
        for k, v in sorted(route_map.items(), key=lambda x: -x[1]["cost"])
    ]

    # 配置（超限降级）
    pricing = cost_service.get_pricing()
    limit = cost_service.daily_cost_limit()
    guard_on = __import__("app.services.system_configs", fromlist=["get_config_bool"]).get_config_bool(
        "cost_guard_enabled", True
    )

    return {
        "today_cost": round(today_cost, 4),
        "daily": daily_rows,
        "by_model": model_rows,
        "by_route": route_rows,
        "config": {
            "daily_cost_limit": limit,
            "cost_guard_enabled": guard_on,
            "pricing": pricing,
        },
    }


@router.put("/{message_id}/review")
def review_feedback(
    message_id: str, req: ReviewRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """复核反馈：approved=入训练集 / rejected=弃用 / pending=待定"""
    if req.status not in REVIEW_STATUSES:
        raise HTTPException(status_code=400, detail=f"status 应为 {REVIEW_STATUSES}")
    m = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if not m or m.feedback is None:
        raise HTTPException(status_code=404, detail="反馈不存在")
    m.review_status = req.status
    _audit(db, admin, "review_feedback", {"message_id": message_id, "feedback": m.feedback, "status": req.status})
    db.commit()
    return {"ok": True, "status": req.status}


@router.get("/export")
def export_feedbacks(
    review_status: str = "approved",  # 默认导出复核通过的
    fb_type: str = "",  # like / dislike / 空=全部
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """导出标注训练集（JSONL）：每行一条 {question, answer, feedback, reason, route, model}
    只导出复核通过的反馈消息，并配对同一会话内紧邻其前的用户问题"""
    if review_status not in REVIEW_STATUSES:
        review_status = "approved"
    q = db.query(ChatMessage).filter(
        ChatMessage.is_deleted.is_(False),
        ChatMessage.feedback.isnot(None),
        ChatMessage.review_status == review_status,
        ChatMessage.role == "assistant",
    )
    if fb_type:
        q = q.filter(ChatMessage.feedback == fb_type)
    targets = q.order_by(ChatMessage.created_at.desc()).all()

    pairs: list[dict] = []
    for m in targets:
        # 同会话内、早于该回答的最后一条用户问题（成对组装）
        q_user = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.session_id == m.session_id,
                ChatMessage.role == "user",
                ChatMessage.created_at < m.created_at,
                ChatMessage.is_deleted.is_(False),
            )
            .order_by(ChatMessage.created_at.desc())
            .first()
        )
        if not q_user:
            continue
        rl = (
            db.query(ChatRouterLog)
            .filter(ChatRouterLog.session_id == m.session_id)
            .order_by(ChatRouterLog.created_at.desc())
            .first()
        )
        pairs.append(
            {
                "question": q_user.content,
                "answer": m.content,
                "feedback": m.feedback,
                "reason": m.feedback_reason or "",
                "route": rl.route_path if rl else "",
                "model": rl.model_used if rl else "",
            }
        )

    buf = io.StringIO()
    for p in pairs:
        buf.write(json.dumps(p, ensure_ascii=False) + "\n")
    data = buf.getvalue()
    filename = f"feedback_dataset_{review_status}.jsonl"
    return Response(
        content=data,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
