"""技能管理 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models.document import Skill, SkillTask
from app.api.auth import get_current_user_id
from app.config import settings
from app.utils.logger import get_logger
import httpx
import json
from datetime import datetime

logger = get_logger("skills")
router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillCreate(BaseModel):
    name: str
    category: str = ""
    priority: int = 1
    goal: str = ""


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    goal: Optional[str] = None
    resources: Optional[list] = None
    verification: Optional[str] = None
    notes: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    completed: Optional[bool] = None
    guide: Optional[list] = None
    resources: Optional[list] = None
    notes: Optional[str] = None


def _owned_skill(db: Session, user_id: str, skill_id: str) -> Skill | None:
    """查询技能并校验归属（NULL 存量兼容可见，他人技能不可操作）"""
    skill = db.query(Skill).filter(Skill.is_deleted == False, Skill.id == skill_id).first()
    if not skill:
        return None
    if skill.user_id != user_id:
        return None
    return skill


def calc_progress(skill_id: str, db: Session):
    total = db.query(SkillTask).filter(SkillTask.skill_id == skill_id, SkillTask.is_deleted == False).count()
    done = (
        db.query(SkillTask)
        .filter(
            SkillTask.skill_id == skill_id,
            SkillTask.completed == True,
            SkillTask.is_deleted == False,
        )
        .count()
    )
    return int(done / total * 100) if total > 0 else 0


def serialize_skill(s: Skill, db: Session):
    tasks = (
        db.query(SkillTask)
        .filter(SkillTask.skill_id == s.id, SkillTask.is_deleted == False)
        .order_by(SkillTask.sort_order)
        .all()
    )
    return {
        "id": str(s.id),
        "name": s.name,
        "category": s.category,
        "priority": s.priority,
        "status": s.status,
        "goal": s.goal or "",
        "verification": s.verification or "",
        "notes": s.notes or "",
        "resources": s.resources or [],
        "progress": calc_progress(str(s.id), db),
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "completed": t.completed,
                "guide": t.guide or [],
                "resources": t.resources or [],
                "notes": t.notes or "",
            }
            for t in tasks
        ],
    }


@router.get("")
def list_skills(
    sort: str = "progress",
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    q = db.query(Skill).filter(Skill.is_deleted == False, Skill.user_id == user_id)
    if sort == "created":
        q = q.order_by(Skill.created_at.desc())
    else:
        q = q.order_by(Skill.priority.desc(), Skill.created_at.desc())
    skills = q.all()
    return [serialize_skill(s, db) for s in skills]


@router.post("")
def create_skill(
    data: SkillCreate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = Skill(**data.dict(), user_id=user_id)
    db.add(skill)
    db.commit()
    logger.info(f"创建技能: {skill.name}, user={user_id}")
    return {"id": str(skill.id)}


@router.put("/{skill_id}")
def update_skill(
    skill_id: str,
    data: SkillUpdate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = _owned_skill(db, user_id, skill_id)
    if not skill:
        return {"error": "not found"}
    for k, v in data.dict(exclude_unset=True).items():
        setattr(skill, k, v)
    db.commit()
    return {"status": "ok"}


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = _owned_skill(db, user_id, skill_id)
    if skill:
        db.query(SkillTask).filter(SkillTask.skill_id == skill_id).delete()
        skill.is_deleted = True
        db.commit()
        logger.info(f"删除技能: {skill.name}")
    return {"status": "ok"}


@router.post("/{skill_id}/tasks")
def add_task(
    skill_id: str,
    data: dict,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if not _owned_skill(db, user_id, skill_id):
        return {"error": "not found"}
    max_order = db.query(SkillTask).filter(SkillTask.skill_id == skill_id).count()
    task = SkillTask(skill_id=skill_id, title=data.get("title", ""), sort_order=max_order)
    db.add(task)
    db.commit()
    return {"id": str(task.id), "title": task.title}


@router.put("/{skill_id}/tasks/{task_id}")
def update_task(
    skill_id: str,
    task_id: str,
    data: TaskUpdate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if not _owned_skill(db, user_id, skill_id):
        return {"error": "not found"}
    task = db.query(SkillTask).filter(SkillTask.id == task_id, SkillTask.skill_id == skill_id).first()
    if not task:
        return {"error": "not found"}
    for k, v in data.dict(exclude_unset=True).items():
        setattr(task, k, v)
    db.commit()
    return {"status": "ok"}


@router.delete("/{skill_id}/tasks/{task_id}")
def delete_task(
    skill_id: str,
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if not _owned_skill(db, user_id, skill_id):
        return {"error": "not found"}
    task = db.query(SkillTask).filter(SkillTask.id == task_id, SkillTask.skill_id == skill_id).first()
    if task:
        task.is_deleted = True
        task.deleted_at = datetime.utcnow()
        db.commit()
    return {"status": "ok"}


async def call_llm(system_prompt: str, user_prompt: str) -> str:
    logger.info(f"调用 LLM: {user_prompt[:50]}...")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}"},
            json={
                "model": "qwen-plus",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
            },
        )
        data = resp.json()
        result = data["choices"][0]["message"]["content"]
        logger.info(f"LLM 返回: {result[:100]}...")
        return result


@router.post("/{skill_id}/generate_subtasks")
async def generate_subtasks(
    skill_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = _owned_skill(db, user_id, skill_id)
    if not skill:
        return {"error": "not found"}
    system_prompt = '你是一个资深 AI 技术导师。请根据用户的【具体学习目标】，为其拆解出由浅入深的 5-8 个实践子任务。必须严格围绕用户的目标场景定制。每个子任务标题不超过 20 个字，直接输出 JSON 字符串数组，例如 ["任务1", "任务2"]，不要输出任何多余的解释。'
    result = await call_llm(system_prompt, skill.goal or skill.name)
    try:
        tasks = json.loads(result)
        if isinstance(tasks, list):
            db.query(SkillTask).filter(SkillTask.skill_id == skill_id).delete()
            for i, title in enumerate(tasks):
                task = SkillTask(skill_id=skill_id, title=title, sort_order=i)
                db.add(task)
            db.commit()
            logger.info(f"AI 拆解完成: {len(tasks)} 个子任务")
            return {
                "tasks": [
                    {"id": str(t.id), "title": t.title}
                    for t in db.query(SkillTask)
                    .filter(SkillTask.skill_id == skill_id)
                    .order_by(SkillTask.sort_order)
                    .all()
                ]
            }
    except Exception as e:
        logger.error(f"AI 拆解失败: {e}")
        return {"error": f"parse failed: {e}", "raw": result}
    return {"error": "LLM format error", "raw": result}


@router.post("/{skill_id}/generate_resources")
async def generate_resources(
    skill_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = _owned_skill(db, user_id, skill_id)
    if not skill:
        return {"error": "not found"}
    system_prompt = "你是一个 AI 技术导师。只返回你确认真实存在的学习资源链接，绝对不要编造或猜测 URL。优先推荐官方文档（docs.*.com）、GitHub 仓库、知名教程网站。返回 JSON 数组，包含 title、url、description、type 字段。只返回 JSON。"
    tasks = db.query(SkillTask).filter(SkillTask.skill_id == skill_id).all()
    task_texts = [t.title for t in tasks]
    user_prompt = f"Skill: {skill.name}\nSubtasks: {', '.join(task_texts)}"
    result = await call_llm(system_prompt, user_prompt)
    try:
        resources = json.loads(result)
        if isinstance(resources, list):
            skill.resources = (skill.resources or []) + resources
            db.commit()
            return {"resources": skill.resources}
    except Exception as e:
        return {"error": f"parse failed: {e}", "raw": result}
    return {"error": "LLM format error", "raw": result}


@router.post("/{skill_id}/generate_verification")
async def generate_verification(
    skill_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = _owned_skill(db, user_id, skill_id)
    if not skill:
        return {"error": "not found"}
    logger.info(f"生成验收标准: {skill.name}")
    system_prompt = '你是一个 AI 技术导师。根据学习目标，生成 3 条可量化、可检验的验收标准。每条不超过 30 字，直接输出 JSON 字符串数组，例如 ["能独立部署一个 Dify 应用", "知识库召回准确率 > 80%"]。只返回 JSON 数组。'
    result = await call_llm(system_prompt, skill.goal or skill.name)
    try:
        items = json.loads(result)
        if isinstance(items, list):
            skill.verification = "\n".join([f"• {x}" for x in items])
            db.commit()
            logger.info(f"验收标准生成成功: {len(items)} 条")
            return {"verification": skill.verification}
    except Exception as e:
        logger.error(f"验收标准生成失败: {e}")
        return {"error": f"parse failed: {e}", "raw": result}
    return {"error": "LLM format error", "raw": result}


@router.post("/{skill_id}/generate_quiz")
async def generate_quiz(
    skill_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = _owned_skill(db, user_id, skill_id)
    if not skill:
        return {"error": "not found"}
    logger.info(f"生成检验题目: {skill.name}")
    system_prompt = "你是一个 AI 技术导师。根据学习目标出 3 道检验题，包含选择、简答、实操各一道。返回 JSON 数组，每题包含 question、options（选择题选项）、answer。只返回 JSON。"
    result = await call_llm(system_prompt, skill.goal or skill.name)
    try:
        return {"quiz": json.loads(result)}
    except Exception as e:
        return {"error": f"parse failed: {e}", "raw": result}


@router.post("/{skill_id}/tasks/{task_id}/generate_guide")
async def generate_task_guide(
    skill_id: str,
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    skill = _owned_skill(db, user_id, skill_id)
    if not skill:
        return {"error": "skill not found"}
    task = db.query(SkillTask).filter(SkillTask.id == task_id, SkillTask.skill_id == skill_id).first()
    if not task:
        return {"error": "task not found"}
    logger.info(f"生成任务指南: {task.title}")
    system_prompt = '你是一个 AI 技术导师。请为用户生成该任务的操作步骤。返回 JSON 格式：{"guide": [{"step_number": 1, "action": "动作", "target": "链接", "detail": "说明"}], "resources": [{"title": "资源", "url": "链接", "type": "doc/video", "description": "描述"}]}。只返回 JSON。'
    result = await call_llm(system_prompt, f"Skill: {skill.name}\nTask: {task.title}")
    try:
        data = json.loads(result)
        task.guide = data.get("guide", [])
        task.resources = data.get("resources", [])
        db.commit()
        logger.info(f"任务指南生成成功: {len(task.guide)} 步")
        return {
            "task": {
                "id": str(task.id),
                "guide": task.guide,
                "resources": task.resources,
            }
        }
    except Exception as e:
        logger.error(f"任务指南生成失败: {e}")
        return {"error": f"parse failed: {e}", "raw": result}
