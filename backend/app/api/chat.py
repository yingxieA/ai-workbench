"""聊天 API - SSE 流式"""

import asyncio
import time
import json
from collections import defaultdict
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import ChatSession, ChatMessage, Document, ChatShare
from app.api.auth import get_current_user_id
from app.agent.graph import agent_graph
from app.agent.state import AgentState
from app.services.memory_service import (
    get_history_messages,
    build_chat_context,
    cache_put_history,
    get_user_profile,
    profile_to_prompt,
    extract_profile,
)
from app.utils.logger import get_logger

logger = get_logger("chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

# 简单限流：1 分钟内最多 10 次
_rate_limit = defaultdict(list)

# 会话级锁，防止同一会话并发请求
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


def _owned_session_query(db: Session, user_id: str, session_id: str) -> ChatSession | None:
    """查询会话并校验归属（允许存量 NULL 会话兼容可见，但禁止访问他人会话）"""
    session = db.query(ChatSession).filter(ChatSession.is_deleted == False, ChatSession.id == session_id).first()
    if not session:
        return None
    if session.user_id != user_id:
        return None
    return session


@router.get("/sessions")
def list_sessions(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    """列出当前用户的历史会话（兼容存量 NULL 会话）"""
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.is_deleted == False, ChatSession.user_id == user_id)
        .order_by(ChatSession.pinned.desc(), ChatSession.created_at.desc())
        .all()
    )
    logger.info(f"查询会话列表: {len(sessions)} 个 (user={user_id})")
    return [
        {
            "id": str(s.id),
            "title": s.title,
            "pinned": s.pinned,
            "created_at": s.created_at.isoformat(),
        }
        for s in sessions
    ]


@router.post("/sessions/{session_id}/share")
def share_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """生成分享链接（仅本人会话可分享）"""
    session = _owned_session_query(db, user_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    import secrets

    token = secrets.token_urlsafe(16)
    share = ChatShare(session_id=session_id, share_token=token)
    db.add(share)
    db.commit()
    logger.info(f"分享会话: {session_id}, token={token}, user={user_id}")
    return {"url": f"/share/{token}"}


@router.get("/share/{token}")
def get_shared_session(token: str, db: Session = Depends(get_db)):
    """查看分享的对话（公开访问，不需登录）"""
    share = db.query(ChatShare).filter(ChatShare.share_token == token).first()
    if not share:
        return {"error": "分享不存在"}
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.is_deleted == False, ChatMessage.session_id == share.session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    session = db.query(ChatSession).filter(ChatSession.is_deleted == False, ChatSession.id == share.session_id).first()
    return {
        "title": session.title if session else "分享的对话",
        "messages": [{"role": m.role, "content": m.content} for m in messages],
    }


@router.get("/sessions/activity")
def recent_activity(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    """最近动态（仅当前用户）"""
    activities = []
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.is_deleted == False, ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc())
        .limit(5)
        .all()
    )
    for s in sessions:
        activities.append({"time": s.created_at.isoformat(), "text": f'你问了 "{s.title}"'})
    docs = (
        db.query(Document)
        .filter(Document.is_deleted == False, Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .limit(5)
        .all()
    )
    for d in docs:
        activities.append({"time": d.created_at.isoformat(), "text": f"你上传了 《{d.title}》"})
    activities.sort(key=lambda x: x["time"], reverse=True)
    return activities[:6]


@router.get("/sessions/{session_id}/messages")
def get_session_messages(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """获取某会话的历史消息（仅本人）"""
    if not _owned_session_query(db, user_id, session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.is_deleted == False, ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "feedback": getattr(m, "feedback", None),
        }
        for m in messages
    ]


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """删除会话（仅本人）"""
    session = _owned_session_query(db, user_id, session_id)
    if session:
        db.query(ChatMessage).filter(ChatMessage.is_deleted == False, ChatMessage.session_id == session_id).delete()
        db.delete(session)
        db.commit()
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str


@router.put("/sessions/{session_id}")
def rename_session(
    session_id: str,
    req: RenameRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """重命名会话（仅本人）"""
    session = _owned_session_query(db, user_id, session_id)
    if session:
        session.title = req.title
        db.commit()
    return {"ok": True}


@router.put("/sessions/{session_id}/pin")
def pin_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """置顶/取消置顶（仅本人）"""
    session = _owned_session_query(db, user_id, session_id)
    if session:
        session.pinned = not session.pinned
        db.commit()
    return {"ok": True, "pinned": session.pinned if session else False}


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None


@router.post("/stream")
def chat_stream_api(
    req: ChatRequest,
    request: Request,
    authorization: str = Header(""),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host
    if not check_rate(client_ip):
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': '请求过于频繁，请1分钟后再试'})}\n\n"]),
            media_type="text/event-stream",
        )
    # 解析当前用户（未登录则不允许发起会话）
    from app.api.auth import get_current_user_id

    try:
        user_id = get_current_user_id(authorization)
    except HTTPException as e:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': e.detail})}\n\n"]),
            media_type="text/event-stream",
        )

    logger.info(f"收到问题: {req.question[:50]}..., session={req.session_id}, ip={client_ip}, user={user_id}")
    # 1. 创建或获取 session
    if req.session_id:
        session_id = req.session_id
        # 校验归属：他人会话禁止继续对话
        session = _owned_session_query(db, user_id, session_id)
        if not session:
            return StreamingResponse(
                iter([f"data: {json.dumps({'error': '会话不存在'})}\n\n"]),
                media_type="text/event-stream",
            )
    else:
        # 用 LLM 生成 10 字内标题
        try:
            from app.services.llm import chat

            title = chat(
                [
                    {
                        "role": "system",
                        "content": "你是一个标题生成器。请把用户的问题总结成不超过10个字的标题，直接输出标题，不要加引号或标点。",
                    },
                    {"role": "user", "content": req.question},
                ]
            ).strip()
        except Exception as e:
            logger.warning(f"生成标题失败: {e}")
            title = req.question[:20]
        session = ChatSession(title=title, user_id=user_id)
        db.add(session)
        db.commit()
        session_id = str(session.id)

    # 2. 存用户消息
    user_msg = ChatMessage(session_id=session_id, role="user", content=req.question)
    db.add(user_msg)
    db.commit()

    # 3. 取历史：Redis 热缓存优先，miss 回源 PG 回填（P2.5 会话恢复双存储）
    history = get_history_messages(session_id)

    # 3.5 前置过滤层（图外：敏感词 → 长度 → 垃圾流量 → 规则匹配 → 轻量模型）
    from app.agent.prefilter import prefilter as run_prefilter

    # 注意 history[:-1]：最后一条是刚入库的当前提问，重复检测须排除自身
    pre = run_prefilter(req.question, user_id, history[:-1])
    logger.info(f"前置过滤: verdict={pre.verdict} layer={pre.layer} detail={pre.detail} latency={pre.latency_ms}ms")
    if pre.verdict != "pass":
        # direct_reply 落库（正常对话）；reject/ask_more 不落库（审计已留痕）
        if pre.verdict == "direct_reply":
            ai_msg = ChatMessage(session_id=session_id, role="assistant", content=pre.reply or "")
            db.add(ai_msg)
            db.commit()

        def early_gen():
            yield f"data: {_sse({'type': 'session', 'session_id': session_id})}\n\n"
            yield f"data: {_sse({'type': 'context', 'contexts': []})}\n\n"
            if pre.reply:
                yield f"data: {_sse({'type': 'token', 'content': pre.reply})}\n\n"
            yield f"data: {_sse({'type': 'done'})}\n\n"

        return StreamingResponse(early_gen(), media_type="text/event-stream")

    # 4. 组装 AgentState：滑动窗口 + 摘要压缩 + 用户画像注入（P2.5）
    ctx = build_chat_context(session_id, req.question)
    state: AgentState = {
        "question": req.question,
        "history": ctx["history"],  # 窗口内原始消息（不含当前提问）
        "summary": ctx["summary"],  # 窗口外摘要（增量压缩）
        "profile": profile_to_prompt(get_user_profile(user_id)),
        "session_id": session_id,
        "user_id": user_id,
        "started_at": time.time(),
    }

    # 5. SSE 流式返回（前端协议：session / route / tool_confirm / tool_result / context / token / done）
    def event_generator():
        contexts_sent = False
        ctx_meta = []  # 本次检索片段（幻觉检测用，P2.6）
        config = {"configurable": {"thread_id": session_id}}
        # 新提问前：若该会话有挂起的人工确认断点（用户改主意/重新提问），放弃旧断点
        try:
            snap = agent_graph.get_state(config)
            if snap and (getattr(snap, "interrupts", None) or snap.values.get("__interrupt__")):
                agent_graph.delete(config)
                logger.info(f"清理挂起的人工确认断点: session={session_id}")
        except Exception as e:
            logger.warning(f"检查挂起断点失败: {e}")
        try:
            yield f"data: {_sse({'type': 'session', 'session_id': session_id})}\n\n"
            yield "event: ping\ndata: {}\n\n"
            answer = ""
            # stream_mode="custom"：节点通过 StreamWriter 推送的事件
            for event in agent_graph.stream(state, config=config, stream_mode="custom"):
                payload = event
                if isinstance(payload, dict) and "type" in payload:
                    etype = payload["type"]
                    if etype == "route" and payload.get("route") == "direct":
                        yield f"data: {_sse({'type': 'context', 'contexts': []})}\n\n"
                        contexts_sent = True
                    elif etype == "contexts":
                        ctx_meta = payload.get("contexts_meta", []) or []
                        yield f"data: {_sse({'type': 'context', 'contexts': ctx_meta})}\n\n"
                        contexts_sent = True
                    elif etype == "tool_confirm":
                        # 高风险工具人工确认：推送确认卡（流在此中断，等 /confirm 续流）
                        yield f"data: {_sse({'type': 'tool_confirm', 'tool': payload.get('tool'), 'args': payload.get('args'), 'description': payload.get('description'), 'risk_level': payload.get('risk_level')})}\n\n"
                    elif etype == "thinking":
                        yield f"data: {_sse({'type': 'thinking', 'content': payload.get('content', '')})}\n\n"
                    elif etype == "tool_result":
                        yield f"data: {_sse({'type': 'tool_result', 'tool': payload.get('tool'), 'success': payload.get('success'), 'summary': payload.get('summary'), 'error': payload.get('error')})}\n\n"
                    elif etype == "token":
                        token = payload.get("content", "")
                        if not token:
                            continue
                        answer += token
                        yield f"data: {_sse({'type': 'token', 'content': token})}\n\n"
            if not contexts_sent:
                yield f"data: {_sse({'type': 'context', 'contexts': []})}\n\n"
        except Exception as e:
            logger.error(f"Agent 链路异常: {e}")
            yield f"data: {_sse({'error': '服务暂时不可用，请稍后再试'})}\n\n"
            answer = answer or ""
        # 存 AI 回复（中断等待确认时 answer 为空，不落库）
        if answer:
            ai_msg = ChatMessage(session_id=session_id, role="assistant", content=answer)
            db.add(ai_msg)
            db.commit()
            # P2.5：更新 Redis 热缓存 + 异步画像抽取
            try:
                cache_put_history(session_id, get_history_messages(session_id))
            except Exception as e:
                logger.warning(f"更新会话缓存失败: {e}")
            try:
                extract_profile(
                    user_id,
                    [
                        {"role": "user", "content": req.question},
                        {"role": "assistant", "content": answer[:500]},
                    ],
                )
            except Exception as e:
                logger.warning(f"画像抽取异常: {e}")
            # P2.6：RAG 分支异步幻觉检测（LLM as a Judge，不阻塞 SSE 结束）
            if ctx_meta:
                try:
                    from app.services.hallucination_judge import run_judge_async

                    run_judge_async(
                        session_id,
                        req.question,
                        [c.get("content", "") for c in ctx_meta if isinstance(c, dict)],
                        answer,
                    )
                except Exception as e:
                    logger.warning(f"幻觉检测触发失败: {e}")
        yield f"data: {_sse({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class ConfirmRequest(BaseModel):
    session_id: str
    approved: bool


@router.post("/confirm")
def chat_confirm_api(
    req: ConfirmRequest,
    request: Request,
    authorization: str = Header(""),
    db: Session = Depends(get_db),
):
    """高风险工具人工确认：从断点 resume，SSE 返回后续生成内容"""
    from langgraph.types import Command
    from app.api.auth import get_current_user_id as _gid

    try:
        user_id = _gid(authorization)
    except HTTPException as e:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': e.detail})}\n\n"]),
            media_type="text/event-stream",
        )

    session = _owned_session_query(db, user_id, req.session_id)
    if not session:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': '会话不存在'})}\n\n"]),
            media_type="text/event-stream",
        )

    config = {"configurable": {"thread_id": req.session_id}}
    try:
        snap = agent_graph.get_state(config)
        has_interrupt = bool(snap and (getattr(snap, "interrupts", None) or snap.values.get("__interrupt__")))
    except Exception as e:
        logger.warning(f"读取断点失败: {e}")
        has_interrupt = False
    if not has_interrupt:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': '没有待确认的操作'})}\n\n"]),
            media_type="text/event-stream",
        )

    def event_generator():
        answer = ""
        first_confirm_skipped = False  # 续流首次 tool_confirm 是当前确认的重放，跳过（避免前端重复弹卡）
        try:
            for event in agent_graph.stream(Command(resume={"approved": req.approved}), config, stream_mode="custom"):
                payload = event
                if not (isinstance(payload, dict) and "type" in payload):
                    continue
                etype = payload["type"]
                if etype == "tool_confirm":
                    if not first_confirm_skipped:
                        first_confirm_skipped = True  # 重放，跳过
                        continue
                    yield f"data: {_sse({'type': 'tool_confirm', 'tool': payload.get('tool'), 'args': payload.get('args'), 'description': payload.get('description'), 'risk_level': payload.get('risk_level')})}\n\n"
                elif etype == "thinking":
                    yield f"data: {_sse({'type': 'thinking', 'content': payload.get('content', '')})}\n\n"
                elif etype == "tool_result":
                    yield f"data: {_sse({'type': 'tool_result', 'tool': payload.get('tool'), 'success': payload.get('success'), 'summary': payload.get('summary'), 'error': payload.get('error')})}\n\n"
                elif etype == "token":
                    token = payload.get("content", "")
                    if not token:
                        continue
                    answer += token
                    yield f"data: {_sse({'type': 'token', 'content': token})}\n\n"
                elif etype == "contexts":
                    yield f"data: {_sse({'type': 'context', 'contexts': payload.get('contexts_meta', [])})}\n\n"
        except Exception as e:
            logger.error(f"确认续流异常: {e}")
            yield f"data: {_sse({'error': '续流失败，请重新提问'})}\n\n"
            answer = answer or ""
        if answer:
            ai_msg = ChatMessage(session_id=req.session_id, role="assistant", content=answer)
            db.add(ai_msg)
            db.commit()
        yield f"data: {_sse({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/history/{session_id}")
def get_history(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if not _owned_session_query(db, user_id, session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at).all()
    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "feedback": getattr(m, "feedback", None),
        }
        for m in messages
    ]


@router.post("/messages/{message_id}/feedback")
def feedback_message(
    message_id: str,
    body: dict,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    fb_type = body.get("type")  # like / dislike
    reason = body.get("reason", "")
    if fb_type not in ("like", "dislike"):
        raise HTTPException(status_code=400, detail="type 应为 like / dislike")
    msg = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if not msg:
        return {"error": "not found"}
    # 校验消息归属
    if not _owned_session_query(db, user_id, str(msg.session_id)):
        raise HTTPException(status_code=404, detail="消息不存在")
    msg.feedback = fb_type
    msg.feedback_reason = reason
    msg.review_status = "pending"  # 新反馈进入待复核
    db.commit()
    return {"ok": True}


def _sse(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)
