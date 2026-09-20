"""聊天 API - SSE 流式"""
import uuid
import time
import json
from collections import defaultdict
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.llm import chat_stream
from app.services.reranker import rerank
from app.services.retriever import retrieve
from app.models.document import ChatSession, ChatMessage, Document
from app.utils.logger import get_logger

logger = get_logger("chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

CHITCHAT = {"你好", "您好", "hi", "hello", "嗨", "在吗", "早上好", "下午好", "晚上好", "谢谢", "感谢", "再见", "拜拜"}

# 简单限流：1 分钟内最多 10 次
_rate_limit = defaultdict(list)

# 会话级锁，防止同一会话并发请求
import asyncio
_session_locks = {}

def get_session_lock(session_id: str):
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]

def check_rate(client_ip: str):
    now = time.time()
    _rate_limit[client_ip] = [t for t in _rate_limit[client_ip] if now - t < 60]
    if len(_rate_limit[client_ip]) >= 10:
        return False
    _rate_limit[client_ip].append(now)
    return True


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)):
    """列出所有历史会话"""
    sessions = db.query(ChatSession).filter(ChatSession.is_deleted == False).order_by(
        ChatSession.pinned.desc(), ChatSession.created_at.desc()
    ).all()
    logger.info(f"查询会话列表: {len(sessions)} 个")
    return [{"id": str(s.id), "title": s.title, "pinned": s.pinned, "created_at": s.created_at.isoformat()} for s in sessions]


@router.post("/sessions/{session_id}/share")
def share_session(session_id: str, db: Session = Depends(get_db)):
    """生成分享链接"""
    import secrets
    token = secrets.token_urlsafe(16)
    share = ChatShare(session_id=session_id, share_token=token)
    db.add(share)
    db.commit()
    logger.info(f"分享会话: {session_id}, token={token}")
    return {"url": f"/share/{token}"}


@router.get("/share/{token}")
def get_shared_session(token: str, db: Session = Depends(get_db)):
    """查看分享的对话"""
    share = db.query(ChatShare).filter(ChatShare.share_token == token).first()
    if not share:
        return {"error": "分享不存在"}
    messages = db.query(ChatMessage).filter(ChatMessage.is_deleted == False, ChatMessage.session_id == share.session_id).order_by(ChatMessage.created_at).all()
    session = db.query(ChatSession).filter(ChatSession.is_deleted == False, ChatSession.id == share.session_id).first()
    return {
        "title": session.title if session else "分享的对话",
        "messages": [{"role": m.role, "content": m.content} for m in messages]
    }


@router.get("/sessions/activity")
def recent_activity(db: Session = Depends(get_db)):
    """最近动态"""
    from datetime import datetime, timedelta
    activities = []
    sessions = db.query(ChatSession).filter(ChatSession.is_deleted == False).order_by(ChatSession.created_at.desc()).limit(5).all()
    for s in sessions:
        activities.append({"time": s.created_at.isoformat(), "text": f"你问了 \"{s.title}\""})
    docs = db.query(Document).order_by(Document.created_at.desc()).limit(5).all()
    for d in docs:
        activities.append({"time": d.created_at.isoformat(), "text": f"你上传了 《{d.title}》"})
    activities.sort(key=lambda x: x["time"], reverse=True)
    return activities[:6]


@router.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str, db: Session = Depends(get_db)):
    """获取某会话的历史消息"""
    messages = db.query(ChatMessage).filter(ChatMessage.is_deleted == False, ChatMessage.session_id == session_id).order_by(ChatMessage.created_at).all()
    return [{"id": str(m.id), "role": m.role, "content": m.content, "feedback": getattr(m, "feedback", None)} for m in messages]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    """删除会话"""
    session = db.query(ChatSession).filter(ChatSession.is_deleted == False, ChatSession.id == session_id).first()
    if session:
        db.query(ChatMessage).filter(ChatMessage.is_deleted == False, ChatMessage.session_id == session_id).delete()
        db.delete(session)
        db.commit()
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str


@router.put("/sessions/{session_id}")
def rename_session(session_id: str, req: RenameRequest, db: Session = Depends(get_db)):
    """重命名会话"""
    session = db.query(ChatSession).filter(ChatSession.is_deleted == False, ChatSession.id == session_id).first()
    if session:
        session.title = req.title
        db.commit()
    return {"ok": True}


@router.put("/sessions/{session_id}/pin")
def pin_session(session_id: str, db: Session = Depends(get_db)):
    """置顶/取消置顶"""
    session = db.query(ChatSession).filter(ChatSession.is_deleted == False, ChatSession.id == session_id).first()
    if session:
        session.pinned = not session.pinned
        db.commit()
    return {"ok": True, "pinned": session.pinned if session else False}


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None


@router.post("/stream")
def chat_stream_api(req: ChatRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host
    if not check_rate(client_ip):
        return StreamingResponse(iter([f"data: {json.dumps({'error': '请求过于频繁，请1分钟后再试'})}\n\n"]), media_type="text/event-stream")
    logger.info(f"收到问题: {req.question[:50]}..., session={req.session_id}, ip={client_ip}")
    # 1. 创建或获取 session
    if req.session_id:
        session_id = req.session_id
    else:
        # 用 LLM 生成 10 字内标题
        try:
            from app.services.llm import chat
            title = chat([
                {"role": "system", "content": "你是一个标题生成器。请把用户的问题总结成不超过10个字的标题，直接输出标题，不要加引号或标点。"},
                {"role": "user", "content": req.question}
            ]).strip()
        except Exception as e:
            logger.warning(f"生成标题失败: {e}")
            title = req.question[:20]
        session = ChatSession(title=title)
        db.add(session)
        db.commit()
        session_id = str(session.id)

    # 2. 存用户消息
    user_msg = ChatMessage(session_id=session_id, role="user", content=req.question)
    db.add(user_msg)
    db.commit()

    # 3. 取最近 10 条历史
    history = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.created_at.desc()).limit(10).all()
    history = list(reversed(history))

    q = req.question.strip().lower()
    contexts_meta = []
    messages = []

    # 4. 构造 messages
    for h in history[:-1]:
        messages.append({"role": h.role, "content": h.content})

    # 身份问题直接回答，不走 RAG
    if "你是谁" in q or "你的名字" in q or "你叫什么" in q:
        messages.insert(0, {"role": "system", "content": "你是「AI 学习工作台」的智能助手。"})
        messages.append({"role": "user", "content": req.question})
    elif q in CHITCHAT:
        messages.insert(0, {"role": "system", "content": "你是「AI 学习工作台」的智能助手，专注于辅助用户学习 AI 技术。友好回复，不要提及参考资料中的人物或项目。"})
        messages.append({"role": "user", "content": req.question})
    else:
        candidates = retrieve(req.question, top_k=10)
        if candidates:
            docs = [c["content"] for c in candidates]
            reranked = rerank(req.question, docs, top_k=3)
            contexts = [doc for doc, _ in reranked]
            contexts_meta = [
                {
                    "index": i + 1,
                    "title": candidates[i].get("doc_title", f"文档片段 {i+1}"),
                    "content": c[:200] + "...",
                }
                for i, c in enumerate(contexts)
            ]
            context_text = "\n\n".join([f"[[{i + 1}]] {c}" for i, c in enumerate(contexts)])
            system_prompt = f"""你是「AI 学习工作台」的智能助手，专注于辅助用户学习 AI 技术。

【身份铁律】
- 你是 AI 学习工作台的助手，不是任何参考资料中的人物或项目。
- 如果用户问"你是谁"，回答你是 AI 学习工作台的智能助手，绝对禁止把自己代入成资料中的项目或人物。

【回答规则】
1. 必须严格基于参考资料回答。如果资料和问题不相关，直接回复"根据现有知识库无法准确回答该问题"，严禁编造。
2. 引用必须出现在具体结论正后方，使用 [[n]] 双括号。
3. 格式要求：对比/列表用表格，步骤用有序列表，重点加粗。

【排版规范】
- 优先使用扁平化 Markdown 结构，避免嵌套超过 3 层。
- 列表项之间不要加入多余空行，保持紧凑。
- 子列表统一缩进 4 个空格。
- 代码块必须用 ``` 包裹并标注语言。
- **【极度重要】当输出代码时，必须严格使用标准换行符（\\n）格式化代码！**
- **每一行代码必须独立成行！绝对禁止把 import、class、方法定义、注释挤在同一行！**
- **必须严格遵循编程语言的缩进规则（例如 Java 使用 4 个空格缩进）！**

参考资料：
{context_text}"""
            messages.insert(0, {"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": req.question})
        else:
            contexts = []
            contexts_meta = []
            messages.append({"role": "user", "content": req.question})

    # 5. SSE 流式返回
    def event_generator():
        yield f"data: {_sse({'type': 'session', 'session_id': session_id})}\n\n"
        yield f"data: {_sse({'type': 'context', 'contexts': contexts_meta})}\n\n"
        yield f"event: ping\ndata: {{}}\n\n"
        answer = ""
        for token in chat_stream(messages):
            answer += token
            yield f"data: {_sse({'type': 'token', 'content': token})}\n\n"
        # 存 AI 回复
        ai_msg = ChatMessage(session_id=session_id, role="assistant", content=answer)
        db.add(ai_msg)
        db.commit()
        yield f"data: {_sse({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/history/{session_id}")
def get_history(session_id: str, db: Session = Depends(get_db)):
    messages = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.created_at).all()
    return [{"id": str(m.id), "role": m.role, "content": m.content, "feedback": getattr(m, "feedback", None)} for m in messages]


@router.post("/messages/{message_id}/feedback")
def feedback_message(message_id: str, body: dict, db: Session = Depends(get_db)):
    fb_type = body.get("type")  # like / dislike
    reason = body.get("reason", "")
    # 简单实现：直接存到 chat_messages 表的 feedback 字段
    msg = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if not msg:
        return {"error": "not found"}
    msg.feedback = fb_type
    msg.feedback_reason = reason
    db.commit()
    return {"ok": True}


def _sse(data: dict) -> str:
    import json
    return json.dumps(data, ensure_ascii=False)