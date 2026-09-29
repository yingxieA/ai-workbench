"""学习路径 API"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from app.database import get_db
from app.models.document import LearningPath, Skill
from app.services.llm import chat_stream
from app.api.auth import get_current_user_id
from app.utils.logger import get_logger
import json
import uuid

logger = get_logger("learning")
router = APIRouter(prefix="/api/learning", tags=["learning"])


class UpdateNodeRequest(BaseModel):
    node_id: str
    completed: bool


def _owned_path(db: Session, user_id: str, path_id) -> LearningPath | None:
    try:
        pid = uuid.UUID(path_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的路径 ID")
    path = db.query(LearningPath).filter(LearningPath.id == pid, LearningPath.is_deleted == False).first()
    if not path:
        return None
    if path.user_id is not None and path.user_id != user_id:
        return None
    return path


@router.get("")
def list_paths(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    paths = (
        db.query(LearningPath)
        .filter(
            LearningPath.is_deleted == False,
            or_(LearningPath.user_id == user_id, LearningPath.user_id.is_(None)),
        )
        .all()
    )
    return [
        {
            "id": str(p.id),
            "goal": p.goal,
            "nodes": p.nodes,
            "completed_nodes": p.completed_nodes or [],
        }
        for p in paths
    ]


@router.post("/generate")
def generate_path(
    goal: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """根据目标和现有技能生成分阶段学习路径"""
    logger.info(f"生成学习路径: {goal}, user={user_id}")
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
        path = LearningPath(goal=goal, nodes=phases, completed_nodes=[], user_id=user_id)
        db.add(path)
        db.commit()
        logger.info(f"学习路径生成成功: {len(phases)} 个阶段")
        return {"id": str(path.id), "phases": phases, "completed_nodes": []}
    except Exception as e:
        logger.error(f"学习路径生成失败: {e}, raw: {result[:200]}")
        return {"phases": [], "raw": result, "error": str(e)}


@router.put("/{path_id}/node")
def update_node_status(
    path_id: str,
    req: UpdateNodeRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """更新学习节点完成状态（仅本人）"""
    path = _owned_path(db, user_id, path_id)
    if not path:
        raise HTTPException(status_code=404, detail="学习路径不存在")

    completed = path.completed_nodes or []
    if req.completed and req.node_id not in completed:
        completed.append(req.node_id)
    elif not req.completed and req.node_id in completed:
        completed.remove(req.node_id)

    path.completed_nodes = completed
    db.commit()
    logger.info(f"更新节点状态: path={path_id}, node={req.node_id}, completed={req.completed}")
    return {"completed_nodes": completed}


@router.delete("/{path_id}")
def delete_path(
    path_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """删除学习路径（仅本人）"""
    path = _owned_path(db, user_id, path_id)
    if not path:
        raise HTTPException(status_code=404, detail="学习路径不存在")

    path.is_deleted = True
    db.commit()
    logger.info(f"删除学习路径: {path_id}")
    return {"status": "ok"}


class AITutorRequest(BaseModel):
    node_title: str
    node_desc: str


@router.post("/ai-tutor")
def ai_tutor(req: AITutorRequest):
    """AI 帮教：解释学习任务"""
    logger.info(f"AI 帮教: {req.node_title}")

    prompt = f"""你是一个资深的 AI 开发导师。请帮我解释这个学习任务：

任务标题：{req.node_title}
任务描述：{req.node_desc}

请严格按照以下 Markdown 格式回答：

## 这个任务是做什么的
用通俗易懂的话解释，2-3句话。

## 为什么要学这个
用 1. 2. 3. 有序列表列出重要性和应用场景，每个列表项单独一行。

## 怎么学
用 1. 2. 3. 有序列表列出具体的学习步骤，每个步骤单独一行。

## 快速上手
给出一个最小可运行的代码示例，用 ```python 代码块包裹。

**格式要求：**
- 每个标题前后都要有空行
- 每个列表项单独占一行
- 代码块必须用 ```python 和 ``` 包裹
- 语言通俗易懂，适合初学者
- 控制在 500 字左右"""

    def generate():
        for token in chat_stream(prompt):
            # 直接发送 token，保留原始换行
            yield f"data: {token}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
