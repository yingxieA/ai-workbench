"""敏感词 / 分类关键词管理 API（仅 admin）：DB 持久化 + Redis 缓存热更新"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import require_admin
from app.models.user import User
from app.models.document import AuditLog
from app.services.rule_keywords import add_keyword, delete_keyword, list_keywords
from app.utils.logger import get_logger

logger = get_logger("rules")
router = APIRouter(prefix="/api/rule-keywords", tags=["rule-keywords"])

VALID_CATEGORIES = {"sensitive", "classify"}
VALID_LEVELS = {"public", "internal", "secret"}


class KeywordRequest(BaseModel):
    category: str = "sensitive"
    keyword: str
    level: str = "secret"


class DeleteKeywordRequest(BaseModel):
    category: str
    keyword: str


def _audit(db: Session, user: User, action: str, detail: dict):
    try:
        db.add(AuditLog(user_id=user.id, action=action, detail=detail))
    except Exception as e:
        logger.warning(f"写审计日志失败: {e}")


@router.get("")
def get_keywords(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """列出全部关键词（含禁用），供管理界面使用"""
    return {"items": list_keywords()}


@router.post("")
def add_keyword_api(req: KeywordRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """新增/更新关键词（幂等：同 category+keyword 覆盖 level）"""
    req.category = (req.category or "").strip().lower()
    req.keyword = (req.keyword or "").strip()
    if req.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="category 仅支持 sensitive/classify")
    if not req.keyword:
        raise HTTPException(status_code=400, detail="keyword 不能为空")
    if req.level not in VALID_LEVELS:
        raise HTTPException(status_code=400, detail="level 仅支持 public/internal/secret")
    result = add_keyword(req.category, req.keyword, req.level, admin.username)
    _audit(db, admin, "add_keyword", {"category": req.category, "keyword": req.keyword, "level": req.level})
    db.commit()
    return result


@router.delete("")
def delete_keyword_api(req: DeleteKeywordRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """软删除关键词（enabled=FALSE）"""
    result = delete_keyword(req.category, req.keyword)
    _audit(db, admin, "delete_keyword", detail={"category": req.category, "keyword": req.keyword})
    db.commit()
    return result
