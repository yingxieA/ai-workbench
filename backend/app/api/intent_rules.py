# -*- coding: utf-8 -*-
"""固定话术管理 API（仅 admin）：DB 持久化 + Redis 缓存热更新

生效链路：运营界面改话术 → 写 DB → invalidate_rules() 删缓存 → 下个请求回源生效（无需重启）
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app.api.auth import require_admin
from app.models.user import User
from app.models.document import AuditLog
from app.agent.prefilter import invalidate_rules
from app.utils.logger import get_logger

logger = get_logger("intent_rules")
router = APIRouter(prefix="/api/intent-rules", tags=["intent-rules"])

VALID_TRIGGER_TYPES = {"exact", "regex", "keyword"}
VALID_INTENTS = {"chitchat", "identity", "thanks", "farewell", "control", "help"}


class IntentRuleRequest(BaseModel):
    trigger_type: str = "exact"  # exact / regex / keyword
    trigger: str  # 触发文本 / 正则 / 关键词
    reply_template: str  # 固定应答
    intent: str = "chitchat"  # 意图分类
    priority: int = 1  # 同层命中取 priority 高者


class ToggleRequest(BaseModel):
    enabled: bool


class DeleteRequest(BaseModel):
    trigger_type: str
    trigger: str


def _audit(db: Session, user: User, action: str, detail: dict):
    try:
        db.add(AuditLog(user_id=user.id, action=action, detail=detail))
    except Exception as e:
        logger.warning(f"写审计日志失败: {e}")


@router.get("")
def list_intent_rules(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """列出全部规则（含禁用），供管理界面使用"""
    rows = db.execute(
        text(
            "SELECT id, trigger_type, trigger, reply_template, intent, priority, enabled, "
            "created_by, created_at FROM intent_rules "
            "ORDER BY enabled DESC, priority DESC, created_at DESC"
        )
    ).fetchall()
    return {
        "items": [
            {
                "id": str(r[0]),
                "trigger_type": r[1],
                "trigger": r[2],
                "reply_template": r[3],
                "intent": r[4],
                "priority": r[5],
                "enabled": r[6],
                "created_by": r[7],
                "created_at": str(r[8]),
            }
            for r in rows
        ]
    }


@router.post("")
def add_intent_rule(req: IntentRuleRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """新增/更新规则（幂等：同 trigger_type+trigger 覆盖 reply_template/intent/priority）"""
    req.trigger_type = (req.trigger_type or "exact").strip().lower()
    req.trigger = (req.trigger or "").strip()
    req.reply_template = (req.reply_template or "").strip()
    req.intent = (req.intent or "chitchat").strip().lower()
    if req.trigger_type not in VALID_TRIGGER_TYPES:
        raise HTTPException(status_code=400, detail="trigger_type 仅支持 exact/regex/keyword")
    if not req.trigger:
        raise HTTPException(status_code=400, detail="trigger 不能为空")
    if not req.reply_template:
        raise HTTPException(status_code=400, detail="reply_template 不能为空")
    if req.trigger_type == "regex":
        # 校验正则合法性，非法直接 400（避免线上非法正则拖垮匹配）
        import re

        try:
            re.compile(req.trigger)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"非法正则: {e}")
    if req.priority < 0 or req.priority > 100:
        raise HTTPException(status_code=400, detail="priority 取值 0-100")

    exists = db.execute(
        text("SELECT id FROM intent_rules WHERE trigger_type=:t AND trigger=:g"),
        {"t": req.trigger_type, "g": req.trigger},
    ).fetchone()
    if exists:
        db.execute(
            text(
                "UPDATE intent_rules SET reply_template=:r, intent=:i, priority=:p, enabled=TRUE, created_by=:u WHERE id=:id"
            ),
            {"r": req.reply_template, "i": req.intent, "p": req.priority, "u": admin.username, "id": exists[0]},
        )
        action = "update"
    else:
        db.execute(
            text(
                "INSERT INTO intent_rules (trigger_type, trigger, reply_template, intent, priority, enabled, created_by) "
                "VALUES (:t,:g,:r,:i,:p,TRUE,:u)"
            ),
            {
                "t": req.trigger_type,
                "g": req.trigger,
                "r": req.reply_template,
                "i": req.intent,
                "p": req.priority,
                "u": admin.username,
            },
        )
        action = "add"
    _audit(
        db,
        admin,
        "add_intent_rule",
        {
            "action": action,
            "trigger_type": req.trigger_type,
            "trigger": req.trigger,
            "intent": req.intent,
            "priority": req.priority,
        },
    )
    db.commit()
    invalidate_rules()  # 热更新：删 Redis 缓存 + 清进程内缓存
    return {"ok": True, "action": action}


@router.delete("")
def delete_intent_rule(req: DeleteRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """删除规则（软删 enabled=FALSE，保留审计痕迹）"""
    result = db.execute(
        text("UPDATE intent_rules SET enabled=FALSE WHERE trigger_type=:t AND trigger=:g"),
        {"t": req.trigger_type, "g": req.trigger},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="规则不存在")
    _audit(db, admin, "delete_intent_rule", {"trigger_type": req.trigger_type, "trigger": req.trigger})
    db.commit()
    invalidate_rules()
    return {"ok": True}


@router.patch("/{rule_id}")
def toggle_intent_rule(
    rule_id: str, req: ToggleRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """启用 / 停用规则"""
    result = db.execute(
        text("UPDATE intent_rules SET enabled=:e WHERE id=:id"),
        {"e": req.enabled, "id": rule_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="规则不存在")
    _audit(db, admin, "toggle_intent_rule", {"id": rule_id, "enabled": req.enabled})
    db.commit()
    invalidate_rules()
    return {"ok": True}
