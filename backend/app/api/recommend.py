"""推荐继续学习"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.document import Skill, SkillTask
from app.utils.logger import get_logger

logger = get_logger("recommend")
router = APIRouter(prefix="/api/recommend", tags=["recommend"])


@router.get("/continue")
def continue_learning(db: Session = Depends(get_db)):
    """推荐最近未完成的技能"""
    # 找最近更新但没完成的技能
    skills = db.query(Skill).filter(
        Skill.is_deleted == False,
        Skill.status != 'done'
    ).order_by(Skill.updated_at.desc()).limit(3).all()

    result = []
    for s in skills:
        tasks = db.query(SkillTask).filter(
            SkillTask.skill_id == s.id,
            SkillTask.is_deleted == False
        ).all()
        total = len(tasks)
        done = len([t for t in tasks if t.completed])
        result.append({
            "skill_id": str(s.id),
            "title": s.name,
            "goal": s.goal or "",
            "progress": round(done / total * 100) if total > 0 else 0,
            "total_tasks": total,
            "done_tasks": done
        })
    return result
