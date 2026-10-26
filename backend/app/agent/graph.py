"""LangGraph 图组装：super_router → (direct|rag|tool|agent_loop) → generate → audit 落库

- 支持人工确认断点（checkpointer=PostgresSaver，thread_id=session_id，PG 持久化，跨进程重启可恢复）
- 工具池在首次构建图时注册内置 7 工具（注册幂等）
- agent_loop：复杂任务多步推理，图内循环（agent_loop_entry → loop_decide ⇄ loop_execute）
"""

import time

from langgraph.graph import END, StateGraph

from app.agent.state import AgentState
from app.agent.router import super_router
from app.agent.retriever import rag_retrieve
from app.agent.tools_node import tool_execute
from app.agent.loop import agent_loop_entry, loop_decide, loop_execute
from app.agent.generator import generate
from app.database import SessionLocal
from app.utils.logger import get_logger

logger = get_logger("agent_graph")

# 幂等注册内置工具（外部 MCP 工具由管理员按需连接，不在此自动注册）
try:
    from app.agent.tools.builtin import register_builtin_tools

    register_builtin_tools()
except Exception as e:
    logger.error(f"内置工具注册失败: {e}")


def _build_checkpointer(persist: bool = True):
    """Checkpointer 构建：
    - persist=False → MemorySaver（进程内断点/恢复仍可用）。生产问答图使用：
      LangGraph 每次节点完成都会写 checkpoint；PostgresSaver 写入语句在 PG 繁忙/锁等待时挂起，
      导致"节点完成 → 下一节点不执行"（前端一直思考中）。生产问答无跨重启 checkpoint 需求，切 MemorySaver 根治。
    - persist=True → PostgresSaver 持久化（debug 单步 / eval 测评图），
      带 statement_timeout=10s 兜底（写入挂起最多 10s 抛错，绝不无限卡）。
    - setup() 幂等建 checkpoint 表（langgraph_checkpoint / langgraph_checkpoint_writes）
    """
    if not persist:
        from langgraph.checkpoint.memory import MemorySaver

        logger.info("LangGraph checkpointer: MemorySaver（生产问答图，进程内断点）")
        return MemorySaver()
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row
        from app.config import settings

        # timeout=3s：连接获取快速失败（默认 30s，池繁忙时会把请求挂死 ~30s，前端表现"一直思考中"）；
        # max_size=15：并发多轮请求时留足余量，避免池耗尽级联挂起
        pool = ConnectionPool(
            conninfo=settings.DATABASE_URL,
            max_size=15,
            timeout=3,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "options": "-c statement_timeout=10000",  # PG 语句 10s 超时，杜绝写入挂起无限等待
            },
        )
        cp = PostgresSaver(pool)
        cp.setup()
        logger.info("LangGraph checkpointer: PostgresSaver（PG 持久化）")
        return cp
    except Exception as e:
        logger.error(f"PostgresSaver 初始化失败，降级 MemorySaver（重启丢断点）: {e}")
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()


def _audit_node(state: AgentState) -> dict:
    """审计落库：route / confidence / 工具 / 模型 / 降级 / 耗时"""
    started_at = state.get("started_at") or time.time()
    latency = int((time.time() - started_at) * 1000)
    fallback = state.get("fallback_info") or {}
    try:
        from app.models.document import ChatRouterLog
        import uuid as _uuid

        session_uuid = None
        try:
            session_uuid = _uuid.UUID(state.get("session_id")) if state.get("session_id") else None
        except (ValueError, AttributeError):
            session_uuid = None
        db = SessionLocal()
        try:
            db.add(
                ChatRouterLog(
                    user_id=state.get("user_id"),
                    session_id=session_uuid,
                    question=(state.get("question") or "")[:500],
                    route_path=state.get("route"),
                    route_detail=state.get("route_detail"),
                    confidence_score=state.get("confidence"),
                    tool_names=",".join(state.get("tool_calls") or [])[:200],
                    tool_results=len(state.get("tool_results") or []),
                    model_used=state.get("model_used"),
                    fallback_triggered=bool(fallback.get("fallback_triggered")),
                    error_type=fallback.get("error_type"),
                    latency_ms=latency,
                    token_usage=state.get("token_usage") or 0,
                    cost=state.get("cost"),
                    contexts="\n".join((state.get("contexts") or []))[:6000] or None,
                )
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"审计落库失败: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"审计模型导入失败: {e}")
    return {"latency_ms": latency}


def _passthrough_router(state: AgentState) -> dict:
    """测评模式用的直通路由：不调用 LLM router，直接采用 state 预置的 route"""
    from langgraph.config import get_stream_writer

    route = state.get("route", "rag")
    if route not in ("direct", "rag", "tool"):
        route = "rag"
    detail = {
        "direct": "llm_common",
        "rag": "knowledge_qa",
        "tool": "tool_call",
    }[route]
    result = {
        "route": route,
        "confidence": 1.0,
        "route_detail": detail,
        "router_model": "forced",
        "tool_calls": state.get("tool_calls") or (["calculator"] if route == "tool" else []),
    }
    try:
        get_stream_writer()({"type": "route", **result})
    except Exception:
        pass
    return result


def _route_after_decide(state: AgentState) -> str:
    """loop_decide 后走向：完成或超步数 → generate；否则 → loop_execute 继续循环"""
    done = state.get("loop_done")
    step = state.get("loop_step") or 0
    from app.agent.loop import get_max_steps

    if done or step >= get_max_steps():
        return "generate"
    return "loop_execute"


def _route_after_retrieve(state: AgentState) -> str:
    """rag 检索后走向：知识库空结果且已标记转联网兜底 → tool_execute；否则 → generate
    （retriever 在 filtered=0 且联网开关打开时置 route=tool + tool_calls=[web_search]，
     由 tool_execute 执行 web_search 联网兜底，保证「查不到也要有结果返回」）"""
    if not state.get("contexts") and state.get("route") == "tool":
        return "tool_execute"
    return "generate"


def build_graph(force_rag: bool = False, debug: bool = False, persist: bool = True):
    builder = StateGraph(AgentState)
    cp = _build_checkpointer(persist=persist)

    # 测评模式（force_rag=True）：跳过 LLM 意图识别，隔离测检索+生成
    router_node = _passthrough_router if force_rag else super_router
    builder.add_node("super_router", router_node)
    builder.add_node("rag_retrieve", rag_retrieve)
    builder.add_node("tool_execute", tool_execute)
    builder.add_node("agent_loop_entry", agent_loop_entry)
    builder.add_node("loop_decide", loop_decide)
    builder.add_node("loop_execute", loop_execute)
    builder.add_node("generate", generate)
    builder.add_node("audit", _audit_node)

    builder.set_entry_point("super_router")
    builder.add_conditional_edges(
        "super_router",
        lambda s: s.get("route", "rag"),
        {"direct": "generate", "rag": "rag_retrieve", "tool": "tool_execute", "agent_loop": "agent_loop_entry"},
    )
    # rag 检索后：空结果+联网开 → tool_execute 兜底联网；否则 → generate
    builder.add_conditional_edges(
        "rag_retrieve",
        _route_after_retrieve,
        {"generate": "generate", "tool_execute": "tool_execute"},
    )
    builder.add_edge("tool_execute", "generate")
    builder.add_edge("agent_loop_entry", "loop_decide")
    builder.add_conditional_edges(
        "loop_decide",
        _route_after_decide,
        {"generate": "generate", "loop_execute": "loop_execute"},
    )
    builder.add_edge("loop_execute", "loop_decide")
    builder.add_edge("generate", "audit")
    builder.add_edge("audit", END)

    if debug:
        # 调试模式：关键节点前打断（interrupt_before），前端"继续"后从断点续跑（P2 真断点单步）
        #   super_router → rag_retrieve → tool_execute → agent_loop_entry → loop_decide → loop_execute → generate
        return builder.compile(
            checkpointer=cp,
            interrupt_before=[
                "super_router",
                "rag_retrieve",
                "tool_execute",
                "agent_loop_entry",
                "loop_decide",
                "loop_execute",
                "generate",
            ],
        )
    return builder.compile(checkpointer=cp)


# 进程内单例图（生产链路）：persist=False → MemorySaver。
# 根因：LangGraph 每次节点完成都写 checkpoint；PostgresSaver 写入语句在 PG 锁等待/繁忙时挂起，
# 节点永远不推进（super_router 完成 → generate/tool_execute 不执行，前端一直"思考中"）。
# 生产问答无跨重启 checkpoint 需求（人工确认断点进程内可用），切 MemorySaver 彻底消除该卡点。
agent_graph = build_graph(persist=False)
# 调试专用图（P2 单步调试：interrupt_before 关键节点，前端逐步确认；保留 PG 持久化）
debug_graph = build_graph(debug=True)
# 测评专用图（跳过 LLM router，隔离测检索/生成，供 eval_runner 使用；保留 PG 持久化）
eval_graph = build_graph(force_rag=True)
logger.info("LangGraph agent 图构建完成（生产图 + 测评图，含 tool 分支 + 人工确认断点）")
