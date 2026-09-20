"""复习 API - 艾宾浩斯遗忘曲线"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database import get_db
from app.models.document import ReviewItem
from app.utils.logger import get_logger

logger = get_logger("review")
router = APIRouter(prefix="/api/review", tags=["review"])

# 艾宾浩斯间隔（天）
INTERVALS = [1, 3, 7, 15, 30]


class AddReviewRequest(BaseModel):
    source_type: str
    source_id: str = None
    title: str
    content: str = ""


@router.get("/today")
def today_reviews(db: Session = Depends(get_db)):
    """今日需要复习的 items"""
    now = datetime.now()
    items = db.query(ReviewItem).filter(
        ReviewItem.is_deleted == False,
        ReviewItem.next_review_at <= now
    ).order_by(ReviewItem.next_review_at).all()
    logger.info(f"今日复习: {len(items)} 条")
    return [{"id": str(i.id), "title": i.title, "content": i.content, "review_count": i.review_count} for i in items]


@router.post("/{item_id}/done")
def review_done(item_id: str, db: Session = Depends(get_db)):
    """标记复习完成，安排下次复习"""
    item = db.query(ReviewItem).filter(ReviewItem.id == item_id).first()
    if not item:
        return {"error": "not found"}
    item.review_count += 1
    interval = INTERVALS[min(item.review_count - 1, len(INTERVALS) - 1)]
    item.next_review_at = datetime.now() + timedelta(days=interval)
    db.commit()
    logger.info(f"复习完成: {item.title}, 下次: {item.next_review_at}")
    return {"next_review_at": item.next_review_at.isoformat()}


@router.post("/add")
def add_review(req: AddReviewRequest, db: Session = Depends(get_db)):
    """添加复习项"""
    item = ReviewItem(
        source_type=req.source_type,
        title=req.title,
        content=req.content,
        next_review_at=datetime.now() + timedelta(days=1)
    )
    db.add(item)
    db.commit()
    logger.info(f"添加复习项: {req.title}")
    return {"id": str(item.id)}
