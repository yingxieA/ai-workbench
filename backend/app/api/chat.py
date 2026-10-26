"""聊天 API - SSE 流式（对话主链路）。

职责：
- /stream：对话主入口（SSE 流式生成）；
- /confirm：高风险工具人工确认后的续流入口。

基础设施已拆至 app/services/chat/：
- infra.py：限流 + 防重发 + 回答回放缓存；
- sse.py：SSE 协议 + _StreamState + 事件翻译；
- session.py：会话创建/复用 + 标题精修 + 归属映射；
- persistence.py：落库 + 锁外收尾。

本文件保留：请求门卫（鉴权/限流）+ 图外前置过滤 + AgentState 组装 + 端点编排。

不在本文件的：
- 会话 CRUD / 分享 / 历史 / 反馈 → chat_sessions.py；
- 图内节点（router / loop / tools / retriever / generator）→ app/agent/*。

主流程步骤函数（每个可单测）：
_auth_and_rate → _dup_guard → _get_or_create_session → _save_user_message
→ _run_prefilter → _build_state → event_generator（图流 + 事件翻译 + 落库收尾）。
"""

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import ChatMessage
from app.api.chat_sessions import _owned_session_query
from app.api.auth import get_current_user_id
from app.core.errors import extract_error_code
from app.agent.graph import agent_graph
from app.agent.state import AgentState
from app.agent.tracing import begin, clear, flush
from app.agent.cancels import mark_cancel, clear_cancel, next_epoch, is_stale, get_latest_epoch
from app.services.memory_service import (
    get_history_messages,
    build_chat_context,
    get_user_profile,
    profile_to_prompt,
)
from app.services.chat.infra import check_rate, _get_client_ip, _dup_guard, RATE_WINDOW, RATE_LIMIT_PER_MIN
from app.services.chat.sse import _sse, _StreamState, _seq_line, _hb_due, _emit_agent_event
from app.services.chat.session import _get_or_create_session, _save_user_message
from app.services.chat.persistence import _persist_interrupted, _persist_and_cleanup
from app.utils.logger import get_logger

logger = get_logger("chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

_SSE_HB_INTERVAL = 5.0  # 心跳检查间隔（秒）：空窗超 _SSE_HB_GAP(8s) 才补发 ping


def _exc_msg(e) -> str:
    """从 HTTPException 提取用户可读 message。

    detail 兼容两种形态：
    - (ErrorCode, "中文文案") 元组 → 取 [1]；
    - 纯字符串 → 原样返回。
    """
    d = e.detail
    if isinstance(d, tuple) and len(d) == 2:
        return str(d[1])
    return str(d)


def _auth_and_rate(
    request: Request, authorization: str, req: "ChatRequest", req_id: str
) -> tuple[str, str, StreamingResponse | None]:
    """鉴权 + 限流。

    顺序：先鉴权、后限流（对话接口不允许匿名；先拿 user_id 才能按 IP+用户 维度限流）。

    返回 (user_id, client_ip, error_response)。
    error_response 非 None 时调用侧直接返回。

    错误语义（前端据此分流）：
    - 401：鉴权失败 → 续期 → 失败跳登录；
    - 429 + Retry-After：限流 → 提示频繁，不重发、不登出；
    - SSE error 事件统一带 request_id，便于跨日志定位。
    """
    client_ip = _get_client_ip(request)
    try:
        user_id = get_current_user_id(authorization)
    except HTTPException as e:
        logger.warning(f"鉴权失败: ip={client_ip}, q={req.question[:50]}, detail={_exc_msg(e)}, req_id={req_id}")
        return (
            "",
            client_ip,
            StreamingResponse(
                iter(
                    [
                        f"data: {json.dumps({'error_code': extract_error_code(e.detail, getattr(e, 'status_code', 401)), 'error': _exc_msg(e), 'request_id': req_id})}\n\n"
                    ]
                ),
                media_type="text/event-stream",
                status_code=getattr(e, "status_code", 401),
            ),
        )
    if not check_rate(client_ip, user_id):
        logger.warning(
            f"限流拦截: ip={client_ip}, user={user_id}, q={req.question[:50]}, "
            f"窗口 {RATE_WINDOW}s 内超 {RATE_LIMIT_PER_MIN} 次, req_id={req_id}"
        )
        return (
            "",
            client_ip,
            StreamingResponse(
                iter(
                    [
                        f"data: {json.dumps({'error_code': 'CHAT_RATE_LIMITED', 'error': f'请求过于频繁，请稍后再试（{RATE_WINDOW} 秒上限 {RATE_LIMIT_PER_MIN} 次）', 'request_id': req_id})}\n\n"
                    ]
                ),
                media_type="text/event-stream",
                status_code=429,
                headers={"Retry-After": str(RATE_WINDOW)},
            ),
        )
    return user_id, client_ip, None


def _run_prefilter(
    db: Session, session_id: str, user_id: str, req: "ChatRequest", epoch: int
) -> tuple[object, StreamingResponse | None]:
    """图外前置过滤：敏感词 → 长度 → 垃圾流量 → 规则匹配 → 轻量模型。

    - verdict=pass：返回 (pre, None)，继续主链路；
    - verdict=direct_reply：落库固定话术 + 返回 early SSE（带 generation）；
    - verdict=reject/ask_more：不落库（审计已留痕），返回 early SSE。
    """
    from app.agent.prefilter import prefilter as run_prefilter

    history = get_history_messages(session_id)
    logger.info(f"历史获取: n={len(history)} (session={session_id})")
    # history[:-1]：最后一条是刚入库的当前提问，重复检测须排除自身
    pre = run_prefilter(req.question, user_id, history[:-1])
    logger.info(f"前置过滤: verdict={pre.verdict} layer={pre.layer} detail={pre.detail} latency={pre.latency_ms}ms")
    if pre.verdict == "pass":
        return pre, None

    # direct_reply 落库（正常对话）；reject/ask_more 不落库（审计已留痕）
    if pre.verdict == "direct_reply":
        # source=prefilter：便于后续统计误杀率（用户差评这类回复可归因到 prefilter 层）
        db.add(ChatMessage(session_id=session_id, role="assistant", content=pre.reply or "", source="prefilter"))
        db.commit()

    def early_gen():
        yield f"data: {_sse({'type': 'session', 'session_id': session_id, 'generation': epoch})}\n\n"
        yield f"data: {_sse({'type': 'context', 'contexts': [], 'generation': epoch})}\n\n"
        if pre.reply:
            yield f"data: {_sse({'type': 'token', 'content': pre.reply, 'generation': epoch})}\n\n"
        yield f"data: {_sse({'type': 'done', 'generation': epoch})}\n\n"

    return pre, StreamingResponse(early_gen(), media_type="text/event-stream")


def _build_state(db: Session, user_id: str, req: "ChatRequest", session_id: str, epoch: int) -> AgentState:
    """组装 AgentState。

    组成：
    - question：当前提问；
    - history/summary：滑动窗口 + 窗口外摘要；
    - profile：用户画像 prompt；
    - session_id/user_id/started_at/epoch；
    - options：前端功能开关（web_search/deep_think）。
    """
    _t0 = time.time()
    ctx = build_chat_context(session_id, req.question)
    logger.info(
        f"上下文构建: latency={int((time.time() - _t0) * 1000)}ms "
        f"history_in_window={len(ctx.get('history', []))} summary_len={len(ctx.get('summary', '') or '')} "
        f"(session={session_id})"
    )
    return {
        "question": req.question,
        "history": ctx["history"],  # 窗口内原始消息（不含当前提问）
        "summary": ctx["summary"],  # 窗口外摘要（增量压缩）
        "profile": profile_to_prompt(get_user_profile(user_id)),
        "session_id": session_id,
        "user_id": user_id,
        "started_at": time.time(),
        "epoch": epoch,
        "options": {"web_search": req.web_search, "deep_think": req.deep_think},  # 前端功能开关
    }


def _try_resume_epoch(session_id: str, question: str) -> int | None:
    """断线续流探测：最新 epoch 是否有「未完成」的 checkpoint（图未跑完可续跑）。

    返回可续的 epoch；无 → None（走正常新跑）：
    - 无 checkpoint / 图已跑完（next 空）→ 不可续（走回放或重跑）；
    - 有 pending interrupt（人工确认待办）→ 不可续（应由 /confirm 续）；
    - checkpoint 里的 question 与本次请求不一致（期间有新问题接管，断点属于别的代际）
      → 不可续（防止旧问题重试续到新问题的断点）。
    """
    try:
        epoch = get_latest_epoch(session_id)
        cfg = {"configurable": {"thread_id": f"{session_id}:{epoch}"}}
        snap = agent_graph.get_state(cfg)
        if snap is None:
            return None
        if (getattr(snap, "next", ()) or ()) and not getattr(snap, "interrupts", None):
            sv = snap.values or {}
            if sv.get("question") == question:
                return epoch
    except Exception as e:
        logger.warning(f"续流探测失败: {e}")
    return None


class ChatRequest(BaseModel):
    """对话请求体。"""

    question: str
    session_id: str | None = None
    client_request_id: str | None = None  # 前端幂等键：每次发送一个 ID，重试复用
    web_search: bool = False  # 联网搜索开关：false 时 router 禁用 web_search
    deep_think: bool = False  # 深度思考开关：false 时 agent_loop 降级 rag/direct
    is_retry: bool = False  # 断线重试/401 续期标记：同幂等键命中防重窗口时放行
    is_new_chat: bool = False  # "新开对话"标记：跳过 _qsess 归属兜底直接新建


@router.post("/stream")
def chat_stream_api(
    req: ChatRequest,
    request: Request,
    authorization: str = Header(""),
    db: Session = Depends(get_db),
):
    """对话主链路：鉴权 → 防重发 → 会话 → 入库 → 前置过滤 → 构图执行 → SSE → 落库。

    同步前置流程（每步可单测）：
    1. _auth_and_rate：鉴权 + 限流；
    2. _dup_guard：防重发；
    3. _get_or_create_session：会话创建/复用；
    4. _save_user_message：用户消息幂等入库；
    5. next_epoch：分配代际（提前到前置过滤前，early SSE 也带 generation）；
    6. _run_prefilter：图外安全闸门（direct_reply 直接返回 early SSE）；
    7. _build_state：组装 AgentState；
    8. StreamingResponse(event_generator)：SSE 流式执行。
    """
    # 0. 生成 request_id（跨日志定位单次请求，随 SSE error / 日志透传）
    req_id = uuid.uuid4().hex[:8]

    # 0.1 鉴权 + 限流（未登录不允许发起会话；限流按 IP+用户 维度，超限回 429）
    user_id, client_ip, err = _auth_and_rate(request, authorization, req, req_id)
    if err:
        return err
    logger.info(
        f"收到问题: {req.question[:50]}..., session={req.session_id}, ip={client_ip}, user={user_id}, req_id={req_id}"
    )

    # 0.5 防重发死循环兜底（Redis 优先，多 worker 全局一致）
    err = _dup_guard(req, req_id)
    if err:
        return err

    # 0.6 断线续流：is_retry + 最新 epoch 图未完成 → 复用断点续跑
    # （跳过会话创建/用户消息入库(已入库)/prefilter(同 rid 已通过)/构图(checkpoint 有 state)）
    resume_epoch = None
    if req.is_retry and req.session_id:
        resume_epoch = _try_resume_epoch(req.session_id, req.question)

    if resume_epoch is not None:
        session_id = req.session_id
        epoch = resume_epoch
        logger.info(f"断线续流: session={session_id} epoch={epoch} 复用断点 req_id={req_id}")
    else:
        # 1. 创建或获取 session
        session_id, err = _get_or_create_session(db, user_id, req)
        if err:
            return err

        # 2. 存用户消息（幂等：优先幂等键 ON CONFLICT，兼容内容+时间窗）
        _save_user_message(db, session_id, req.question, req.client_request_id)

        # 3. 分配代际（提前到前置过滤前：direct_reply SSE 也带 generation，前端按代际过滤旧流残余）
        epoch = next_epoch(session_id)

        # 4. 前置过滤层（图外）
        pre, err = _run_prefilter(db, session_id, user_id, req, epoch)
        if err:
            return err

        # 5. 组装 AgentState
        state = _build_state(db, user_id, req, session_id, epoch)

    # 6. SSE 流式返回（协议：session / route / tool_confirm / tool_result / context / token / done）
    # async event_generator + astream —— 图节点全 async，task.cancel 可协程级强杀旧请求
    async def event_generator():
        """SSE 生成器：图流 + 心跳 + 事件翻译 + 落库收尾。

        结构：
        - 心跳 task（_hb）+ 图流 task（_stream）并行，主循环只 await hb_q；
        - 心跳不受图节点阻塞影响，空窗期给客户端喂字节；
        - GeneratorExit（客户端断开）时标记 _interrupted + mark_cancel(epoch+1) + 落库已生成内容；
        - finally 取消两个 task；仅"正常完成且本代际仍最新"才 clear_cancel；
        - 落库前 is_stale 校验，旧代际结果作废。
        """
        st = _StreamState()
        # P1：thread_id 带 epoch，新旧图状态物理隔离
        config = {"configurable": {"thread_id": f"{session_id}:{epoch}"}}
        collector = begin(uuid.uuid4().hex, session_id, user_id, req.question)

        # 断点清理：
        # - 新会话（前端未带 session_id，本请求新建 thread）→ 无旧断点，跳过清理（省一次调用，避免拖慢首包）
        # - 续问（前端带 session_id）→ 清本会话全部旧代际 checkpoint，防 MemorySaver 泄漏
        if req.session_id and resume_epoch is None:
            _t_cp = time.time()
            try:
                for _e in range(1, epoch + 1):
                    agent_graph.checkpointer.delete_thread(thread_id=f"{session_id}:{_e}")
                logger.info(
                    f"清理会话旧状态/断点: session={session_id} epoch<= {epoch} latency={int((time.time() - _t_cp) * 1000)}ms"
                )
            except Exception as e:
                logger.warning(f"清理断点失败: {e}")

        # P1 乐观并发：只取消旧代际，自己不受影响
        mark_cancel(session_id, upto_epoch=epoch)
        logger.info(f"会话新代际开始: session={session_id} epoch={epoch}（已标记旧代际取消）")
        _interrupted = False  # 客户端断开/停止标记（finally 据此保留取消信号）

        # 心跳/图流解耦：图流在独立 task 中运行，主循环只等 hb_q
        hb_q: "asyncio.Queue" = asyncio.Queue()

        async def _hb():
            """心跳 task：距上次事件超过 _SSE_HB_GAP 秒 → 推 hb 事件。"""
            while True:
                await asyncio.sleep(_SSE_HB_INTERVAL)
                if _hb_due(st.last_event_t, time.time()):
                    try:
                        hb_q.put_nowait(("hb", None))
                    except Exception:
                        return

        async def _stream():
            """图流 task：astream 消费图事件，投递到 hb_q；结束/异常投递 done/err。"""
            try:
                # resume 模式：astream(None, config) 从 checkpoint 继续（已跑完节点不重跑）
                async for event in agent_graph.astream(
                    None if resume_epoch is not None else state, config=config, stream_mode="custom"
                ):
                    hb_q.put_nowait(("ev", event))
                hb_q.put_nowait(("done", None))
            except Exception as e:
                hb_q.put_nowait(("err", e))

        hb_task = asyncio.create_task(_hb())
        stream_task = asyncio.create_task(_stream())
        try:
            yield _seq_line(st, f"data: {_sse({'type': 'session', 'session_id': session_id, 'generation': epoch})}\n\n")
            st.last_event_t = time.time()
            yield _seq_line(st, "event: ping\ndata: {}\n\n")
            st.last_event_t = time.time()
            _graph_t0 = time.time()
            logger.info(f"图开始执行: session={session_id} route=待定")
            # 埋点：非 token 事件打间隔耗时（gap=距上一事件毫秒），定位"节点完成→下一节点事件"之间的卡点
            _last_ev_t = time.time()
            _ev_i = 0
            while True:
                kind, item = await hb_q.get()
                if kind == "done":
                    break
                if kind == "err":
                    raise item
                if kind == "hb":
                    # 空窗心跳：图节点阻塞期间也能给客户端喂字节（防中间代理超时）
                    yield _seq_line(st, "event: ping\ndata: {}\n\n")
                    st.last_event_t = time.time()
                    continue
                payload = item
                if isinstance(payload, dict) and "type" in payload:
                    _ev_i += 1
                    etype = payload["type"]
                    if etype != "token":
                        _now = time.time()
                        logger.info(
                            f"图事件#{_ev_i}: {etype} gap={int((_now - _last_ev_t) * 1000)}ms (session={session_id})"
                        )
                        _last_ev_t = _now
                    for line in _emit_agent_event(payload, st, generation=epoch):
                        yield _seq_line(st, line)
                        st.last_event_t = time.time()
            if not st.contexts_sent:
                yield _seq_line(st, f"data: {_sse({'type': 'context', 'contexts': [], 'generation': epoch})}\n\n")
                st.last_event_t = time.time()
            logger.info(
                f"图执行完成: session={session_id} latency={int((time.time() - _graph_t0) * 1000)}ms answer_len={len(st.answer or '')}"
            )
        except Exception as e:
            logger.error(f"Agent 链路异常: {e}")
            yield _seq_line(
                st,
                f"data: {_sse({'error_code': 'CHAT_SERVICE_UNAVAILABLE', 'error': '服务暂时不可用，请稍后再试', 'generation': epoch})}\n\n",
            )
            st.answer = st.answer or ""
        except GeneratorExit:
            # 客户端断开（刷新/取消/超时中止）：链路被中断。
            # - 标记取消「本代际及更旧」（upto_epoch=epoch+1），不动更新代际；
            # - 已生成内容照常落库（不丢用户已看到的半截回答）；
            # - raise 重新抛出，符合生成器语义。
            _interrupted = True
            if resume_epoch is None:
                # 正常请求断开：取消本代际及更旧（内容由半截落库兜底）
                mark_cancel(session_id, upto_epoch=epoch + 1)
            else:
                # 续流请求断开：不取消本代际，保留 checkpoint 供再次续流（半截仍落库防丢）
                logger.info(f"续流再次中断: session={session_id}, answer_len={len(st.answer or '')}")
            _persist_interrupted(db, session_id, epoch, st.answer, st, req.client_request_id)
            raise
        finally:
            # 不 await：GeneratorExit 下 finally 不能挂起，取消在事件循环后台完成
            hb_task.cancel()
            stream_task.cancel()
            # 仅"正常完成且本代际仍最新"才清除取消标记
            # （断开/停止时保留，让旧工具线程靠 is_cancelled 秒级感知退出）
            if not _interrupted and not is_stale(session_id, epoch):
                clear_cancel(session_id)

        # 落库前校验：旧代际结果作废，不污染历史
        if st.answer and is_stale(session_id, epoch):
            logger.warning(f"旧代际结果丢弃不落库: session={session_id} epoch={epoch}")
            st.answer = ""
        # 锁外收尾：落库 + 缓存 + 画像 + 幻觉检测
        _persist_and_cleanup(db, session_id, user_id, req.question, st, collector, req.client_request_id)
        yield _seq_line(st, f"data: {_sse({'type': 'done', 'generation': epoch})}\n\n")

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
    """高风险工具人工确认：从断点 resume，SSE 返回后续生成内容。

    流程：
    1. 鉴权 + 会话归属校验；
    2. 定位最新代际 checkpoint，判断是否有待确认的 interrupt；
    3. Command(resume={"approved": ...}) 续流；
    4. 跳过首次 tool_confirm（当前确认的重放），避免前端重复弹卡；
    5. 转发 thinking/tool_result/token/contexts（带 generation）；
    6. 落库 assistant + flush + clear。

    注意：本入口目前未接入 /stream 的限流/防重发/心跳/代际 stale 校验，
    属于已知差异，后续可对齐。
    """
    from langgraph.types import Command

    try:
        user_id = get_current_user_id(authorization)
    except HTTPException as e:
        return StreamingResponse(
            iter(
                [
                    f"data: {json.dumps({'error_code': extract_error_code(e.detail, getattr(e, 'status_code', 401)), 'error': _exc_msg(e)})}\n\n"
                ]
            ),
            media_type="text/event-stream",
            status_code=getattr(e, "status_code", 401),
        )

    session = _owned_session_query(db, user_id, req.session_id)
    if not session:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error_code': 'CHAT_SESSION_NOT_FOUND', 'error': '会话不存在'})}\n\n"]),
            media_type="text/event-stream",
        )

    # P1：续流定位当前代际的 checkpoint（thread_id 带 epoch 后缀）
    _epoch = get_latest_epoch(req.session_id)
    config = {"configurable": {"thread_id": f"{req.session_id}:{_epoch}"}}
    try:
        snap = agent_graph.get_state(config)
        has_interrupt = bool(snap and (getattr(snap, "interrupts", None) or snap.values.get("__interrupt__")))
    except Exception as e:
        logger.warning(f"读取断点失败: {e}")
        has_interrupt = False
    if not has_interrupt:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error_code': 'CHAT_NO_PENDING_CONFIRM', 'error': '没有待确认的操作'})}\n\n"]),
            media_type="text/event-stream",
        )

    async def event_generator():
        """续流生成器：resume → 转发事件 → 落库。"""
        st = _StreamState()
        first_confirm_skipped = False  # 续流首次 tool_confirm 是当前确认的重放，跳过（避免前端重复弹卡）
        collector = begin(uuid.uuid4().hex, req.session_id, user_id, "人工确认续流")
        try:
            async for event in agent_graph.astream(
                Command(resume={"approved": req.approved}), config, stream_mode="custom"
            ):
                payload = event
                if not (isinstance(payload, dict) and "type" in payload):
                    continue
                etype = payload["type"]
                if etype == "tool_confirm":
                    if not first_confirm_skipped:
                        first_confirm_skipped = True  # 重放，跳过
                        continue
                    yield f"data: {_sse({'type': 'tool_confirm', 'tool': payload.get('tool'), 'args': payload.get('args'), 'description': payload.get('description'), 'risk_level': payload.get('risk_level'), 'generation': _epoch})}\n\n"
                elif etype == "thinking":
                    yield f"data: {_sse({'type': 'thinking', 'content': payload.get('content', ''), 'step_kind': payload.get('step_kind', ''), 'tool': payload.get('tool'), 'url': payload.get('url'), 'generation': _epoch})}\n\n"
                elif etype == "tool_result":
                    yield f"data: {_sse({'type': 'tool_result', 'tool': payload.get('tool'), 'success': payload.get('success'), 'summary': payload.get('summary'), 'error': payload.get('error'), 'generation': _epoch})}\n\n"
                elif etype == "token":
                    token = payload.get("content", "")
                    if not token:
                        continue
                    st.answer += token
                    yield f"data: {_sse({'type': 'token', 'content': token, 'generation': _epoch})}\n\n"
                elif etype == "contexts":
                    yield f"data: {_sse({'type': 'context', 'contexts': payload.get('contexts_meta', []), 'generation': _epoch})}\n\n"
        except Exception as e:
            logger.error(f"确认续流异常: {e}")
            yield f"data: {_sse({'error': '续流失败，请重新提问', 'generation': _epoch})}\n\n"
            st.answer = st.answer or ""
        if st.answer:
            try:
                db.add(ChatMessage(session_id=req.session_id, role="assistant", content=st.answer))
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"确认续流落库失败: {e}")
        try:
            flush(collector, db, req.session_id, user_id)
        except Exception as e:
            logger.warning(f"调试轨迹落库失败: {e}")
        clear(req.session_id)
        yield f"data: {_sse({'type': 'done', 'generation': _epoch})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
