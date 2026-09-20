"""学习路径 API"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.models.document import LearningPath, Skill
from app.services.llm import chat_stream
from app.utils.logger import get_logger
import json

logger = get_logger("learning")
router = APIRouter(prefix="/api/learning", tags=["learning"])


@router.get("")
def list_paths(db: Session = Depends(get_db)):
    paths = db.query(LearningPath).filter(LearningPath.is_deleted == False).all()
    return [{"id": str(p.id), "goal": p.goal, "nodes": p.nodes} for p in paths]


@router.post("/generate")
def generate_path(goal: str, db: Session = Depends(get_db)):
    """根据目标和现有技能生成分阶段学习路径"""
    logger.info(f"生成学习路径: {goal}")
    skills = db.query(Skill).filter(Skill.is_deleted == False).all()
    skill_list = "\n".join([f"- {s.name} ({s.status})" for s in skills])

    prompt = f"""目标：{goal}
现有技能：
{skill_list}

请按"基础筑基期 -> 核心攻坚期 -> 实战应用期 -> 体系沉淀期"四个阶段，生成学习路径。
每个阶段包含 goal（阶段目标）和 nodes（学习节点列表）。
每个节点包含 id、title、desc、resources（链接数组）。
严格返回以下 JSON 格式，不要输出任何其他文字：
{{"phases": [{{"phase": "1. 基础筑基期", "goal": "...", "nodes": [{{"id": "n1", "title": "...", "desc": "...", "resources": []}}]}}]}}"""

    result = ""
    for token in chat_stream(prompt):
        result += token

    # 清理 markdown 代码块
    cleaned = result.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        phases = data.get("phases", [])
        if not phases and isinstance(data, list):
            phases = data
        path = LearningPath(goal=goal, nodes=phases)
        db.add(path)
        db.commit()
        logger.info(f"学习路径生成成功: {len(phases)} 个阶段")
        return {"id": str(path.id), "phases": phases}
    except Exception as e:
        logger.error(f"学习路径生成失败: {e}, raw: {result[:200]}")
        return {"phases": [], "raw": result, "error": str(e)}
