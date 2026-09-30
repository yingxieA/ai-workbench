"""Agent Loop 多步推理：复杂任务拆解为多轮 决策→执行→观察 循环

图结构（LangGraph 图内循环，天然支持 checkpoint / interrupt 断点）：
    super_router --(agent_loop)--> agent_loop_entry --> loop_decide
        loop_decide --(done|超步数|超时|死循环)--> generate
        loop_decide --(继续)--> loop_execute --> loop_decide

- loop_decide：LLM 决策（任务是否完成 / 下一步调哪个工具 + 参数），推送 thinking 事件
- loop_execute：jsonschema 校验 → 高风险人工确认（interrupt）→ 执行 → 结果写入轨迹
- 循环控制四道防线（生产级防卡死）：
    ① max_steps 步数上限
    ② 整轮总时长熔断（LOOP_TOTAL_TIMEOUT）
    ③ 死循环检测（同一工具 + 同参数连续 >=2 次）
    ④ 单步 LLM / 单次工具执行超时兜底
- 错误治理：工具失败自动重试（可重试错误）→ 备选工具降级（TOOL_FALLBACK）→ 兜底提示
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from app.agent.llm import chat_complete
from app.agent.state import AgentState
from app.agent.tools.registry import ToolContext, registry, RISK_HIGH
from app.agent.tools.executor import execute_tool
from app.agent.tools.validation import safe_truncate, validate_arguments
from app.utils.logger import get_logger

logger = get_logger("agent_loop")

# ── 循环控制参数（可配置化：后续挪 yaml / 数据库热更）──────────────────
MAX_STEPS = 6  # 最大推理步数（防死循环，第一道防线）
STEP_LLM_TIMEOUT = 40  # 单步 LLM 决策整体超时（秒）。模型链底层各档已 15/20s，
# 此为整链兜底（3 档全卡最坏 ~60s 时强制接管）
TOOL_TIMEOUT = 30  # 单次工具执行超时（秒）
TOOL_RETRY = 1  # 工具失败自动重试次数（仅可重试错误；业务错误不重试）
LOOP_TOTAL_TIMEOUT = 180  # 整轮循环总时长上限（秒），超出即熔断走兜底

# 工具降级映射（配置化扩展点：同功能双源时填入，如天气类双源）
#   格式：{ 主工具名: [备选工具名, ...] }
#   主工具连续失败后自动依次尝试备选，全部失败才记 error 交给模型换策略
TOOL_FALLBACK: dict[str, list[str]] = {
    # 示例（接入双源后取消注释启用）：
    # "amap_maps_weather": ["web_search"],
}

# 可重试错误标记（超时 / 连接 / 限流 / 5xx 等瞬时故障；业务错误不命中）
_RETRYABLE_MARKERS = (
    "timeout",
    "timed out",
    "timedout",
    "connection",
    "connect",
    "refused",
    "closed",
    "503",
    "502",
    "500",
    "429",
    "rate",
    "超时",
    "连接",
)

_DECIDE_PROMPT = """你是多步推理 Agent。用户的任务可能包含多个子步骤，你必须逐个执行、全部完成才能结束。

关键规则（务必遵守）：
1. 先识别用户问题中的所有子任务（"查一下…再算一下…"、"先…然后…"、"对比"、"基于以上结果…"等），每步决策在 reasoning 里先写下"子任务清单"（如 1.查天气 2.查路线 3.对比），然后核对：只要还有未执行的子任务，done 必须为 false。
2. 不要靠模型心算日期、数字、实时信息、网页内容——这些必须调用工具获取。
3. 不要重复调用同一个工具且参数完全一样（上一步已成功取到的结果直接用）；执行失败的步骤要换工具或换参数重试，不要带着失败结果直接 done。
4. 每一步必须选择能推进"尚未完成子任务"的工具；不要反复获取已完成的子任务结果。
5. 全部子任务执行完毕、信息齐全后，才设置 done=true 交给最终回答。
6. 如果目标工具需要"查找型"参数（经纬度、城市编码、POI ID 等），必须先调用地理/检索工具（如 text_search、geo、regeocode）拿到该参数，再调用目标工具；不要直接用地名/自然语言填坐标类参数。

已有执行轨迹（按顺序）：
{trajectory}

当前进度：第 {step} 步 / 最多 {max_steps} 步。

只输出 JSON，不要任何其他文字：
{{"done": true 或 false, "reasoning": "一句话说明为什么", "tool": "工具名 或 null", "args": {{...参数...}}}}

可用工具清单：
{tools_summary}

用户问题：{question}

示例：
Q: 今天是几号，30天后呢
第1步（未执行任何工具）应输出：{{"done": false, "reasoning": "先取今天的日期", "tool": "get_current_time", "args": {{}}}}
第2步（已有今天日期）应输出：{{"done": false, "reasoning": "基于今天日期计算30天后", "tool": "date_calculator", "args": {{"action": "add_days", "days": 30, "base_date": "2026-09-29"}}}}
第3步（两个子任务都完成）应输出：{{"done": true, "reasoning": "两个子任务都已执行完毕", "tool": null, "args": null}}
"""


def _trajectory_text(trace: list) -> str:
    if not trace:
        return "（暂无，这是第一步）"
    lines = []
    for t in trace:
        if t.get("error"):
            lines.append(f"步骤{t['step']}：调用 {t.get('tool')} 失败：{t['error']}")
        else:
            lines.append(f"步骤{t['step']}：调用 {t.get('tool')} → {safe_truncate(str(t.get('result')), 300)}")
    return "\n".join(lines)


def _safe_parse(text: str) -> dict:
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _push(writer, payload: dict) -> None:
    try:
        writer(payload)
    except Exception:
        pass


def _get_writer():
    """安全获取流 writer：非 LangGraph 运行上下文（单测/脚本直调）时返回 no-op"""
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:

        class _Noop:
            def __call__(self, payload: dict) -> None:
                pass

        return _Noop()


def _is_retryable(err: str) -> bool:
    """判断错误是否值得自动重试（瞬时故障重试；业务错误不重试）"""
    low = (err or "").lower()
    return any(m in low for m in _RETRYABLE_MARKERS)


def _call_with_timeout(fn, timeout: float, default_error: str) -> dict:
    """把任意同步调用包上超时（线程池 + future.result(timeout)）。

    超时后线程仍在后台运行（同步世界无法强杀），但主流程立即返回超时错误，
    由循环控制接管（重试 / 降级 / 兜底），不会阻塞整轮推理。
    """
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except FutureTimeout:
        return {"success": False, "tool": "?", "result": None, "error": default_error}
    except Exception as e:
        return {"success": False, "tool": "?", "result": None, "error": f"{default_error}: {str(e)[:120]}"}
    finally:
        pool.shutdown(wait=False)  # 不等待后台线程，避免超时后仍被阻塞


# ── 循环控制辅助 ───────────────────────────────────────────────────


def _detect_repeat(trace: list) -> str | None:
    """死循环检测：同一工具 + 同一参数连续出现 >=2 次 → 返回终止原因（否则 None）。

    覆盖"模型反复调用同一工具同一参数"的原地打转场景，
    避免白白耗光 max_steps 后才兜底。
    """
    last_key = None
    count = 0
    for t in trace:
        key = (t.get("tool"), json.dumps(t.get("args") or {}, sort_keys=True, ensure_ascii=False))
        if key == last_key:
            count += 1
        else:
            last_key, count = key, 1
        if count >= 2:
            return f"检测到连续 {count} 次重复调用工具 {t.get('tool')}（参数相同），停止继续尝试"
    return None


def _terminate(writer, reason: str, friendly: str) -> dict:
    """统一兜底出口：推送友好提示 + 结束循环（供三种终止路径复用）"""
    logger.info(f"agent_loop 终止: {reason}")
    _push(writer, {"type": "thinking", "content": friendly})
    return {"loop_done": True, "loop_reasoning": reason}


# ── 节点实现 ─────────────────────────────────────────────────────


def agent_loop_entry(state: AgentState) -> AgentState:
    """初始化循环轨迹（新任务从第 0 步开始；resume 场景保留已有轨迹 + 起始时间）"""
    return {
        "loop_trace": state.get("loop_trace") or [],
        "loop_step": state.get("loop_step") or 0,
        "loop_done": False,
        "loop_pending_tool": None,
        "loop_pending_args": None,
        "loop_started_at": state.get("loop_started_at") or time.time(),
    }


def loop_decide(state: AgentState) -> AgentState:
    """LLM 决策：done / 下一步工具 + 参数（前置四道防线检查）"""
    writer = _get_writer()
    question = state.get("question", "")
    trace = state.get("loop_trace") or []
    step = state.get("loop_step") or 0

    # ① 步数上限防御：决策前先判，超限直接结束
    if step >= MAX_STEPS:
        return _terminate(
            writer,
            f"达到步数上限 {MAX_STEPS}",
            f"已尝试 {step} 步仍未完成全部子任务（达到步数上限），先基于已有结果回答",
        )

    # ③ 死循环检测：同一工具 + 同参数连续 >=2 次 → 终止
    repeat = _detect_repeat(trace)
    if repeat:
        return _terminate(writer, repeat, repeat + "，先基于已有结果回答")

    # ② 整轮总时长熔断
    started = state.get("loop_started_at") or time.time()
    if time.time() - started > LOOP_TOTAL_TIMEOUT:
        return _terminate(
            writer,
            f"整轮处理超时（>{LOOP_TOTAL_TIMEOUT}s）",
            f"处理超时（超过 {LOOP_TOTAL_TIMEOUT} 秒），先返回已完成的中间结果",
        )

    prompt = (
        _DECIDE_PROMPT.replace("{trajectory}", _trajectory_text(trace))
        .replace("{step}", str(step))
        .replace("{max_steps}", str(MAX_STEPS))
        .replace("{tools_summary}", registry.summary())
        .replace("{question}", question)
    )
    result: dict = {"loop_done": True, "loop_reasoning": "", "tool": None, "args": None}
    try:
        # ④ 单步 LLM 整体超时兜底（模型链底层各档 15/20s，此为整链 40s 强制接管）
        text, info = _call_with_timeout(
            lambda: chat_complete(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": question},
                ]
            ),
            STEP_LLM_TIMEOUT,
            f"模型决策超时（>{STEP_LLM_TIMEOUT}s）",
        )
        if not text:
            return _terminate(writer, "模型决策超时", "模型决策超时，先基于已有结果回答")

        parsed = _safe_parse(text)
        done = bool(parsed.get("done", True))
        tool = parsed.get("tool")
        args = parsed.get("args")
        reasoning = str(parsed.get("reasoning", ""))
        # 工具校验：必须存在于池中
        if not done:
            if not (isinstance(tool, str) and registry.get(tool)):
                done = True
                reasoning = "未选择合法工具，直接回答"
        _push(
            writer,
            {
                "type": "thinking",
                "content": reasoning or ("继续调用 " + tool if tool else "已完成"),
            },
        )
        logger.info(
            f"loop_decide: step={step}, done={done}, tool={tool}, "
            f"model={info.get('model_used')}, latency={info.get('latency_ms')}ms"
        )
        from app.agent.tracing import emit as _trace_emit

        _trace_emit(
            state,
            "loop_decide",
            {
                "step": step,
                "done": done,
                "tool": tool,
                "args": args,
                "reasoning": reasoning,
                "raw": (text or "")[:1500],
            },
            model=info.get("model_used"),
            latency_ms=info.get("latency_ms"),
        )
        result = {
            "loop_done": done,
            "loop_reasoning": reasoning,
            "loop_pending_tool": tool if not done else None,
            "loop_pending_args": args if not done else None,
            "loop_model": info.get("model_used"),
        }
    except Exception as e:
        logger.error(f"loop_decide 失败: {e}，直接结束循环")
        _push(writer, {"type": "thinking", "content": "决策失败，先基于已有结果回答"})
        result = {"loop_done": True, "loop_reasoning": f"决策失败: {str(e)[:80]}"}
    return result


def _execute_one(tool: str, args: dict, ctx: ToolContext, timeout: float) -> dict:
    """单次带超时的工具执行，返回统一结果结构"""
    r = _call_with_timeout(lambda: execute_tool(tool, args, ctx), timeout, f"工具执行超时（>{timeout}s）")
    r["tool"] = tool
    return r


def loop_execute(state: AgentState) -> AgentState:
    """执行上一步决策的工具：校验 → 高风险人工确认 → 执行（超时+重试+降级）→ 写入轨迹"""
    writer = _get_writer()
    question = state.get("question", "")
    tool = state.get("loop_pending_tool")
    args = state.get("loop_pending_args") or {}
    trace = list(state.get("loop_trace") or [])
    step = (state.get("loop_step") or 0) + 1  # 本次执行记为第 step 步
    ctx = ToolContext(user_id=state.get("user_id"), session_id=state.get("session_id"), question=question)

    if not tool:
        return {"loop_step": step, "loop_pending_tool": None, "loop_pending_args": None}

    spec = registry.get(tool)
    entry = {"step": step, "tool": tool, "args": args}
    if spec is None:
        entry["error"] = f"工具不存在: {tool}"
        trace.append(entry)
        _push(writer, {"type": "tool_result", "tool": tool, "success": False, "summary": "", "error": entry["error"]})
        return {"loop_trace": trace, "loop_step": step, "loop_pending_tool": None, "loop_pending_args": None}

    # 参数校验（防注入）
    ok, err = validate_arguments(spec, args)
    if not ok:
        entry["error"] = err
        trace.append(entry)
        _push(writer, {"type": "tool_result", "tool": tool, "success": False, "summary": "", "error": err})
        return {"loop_trace": trace, "loop_step": step, "loop_pending_tool": None, "loop_pending_args": None}

    # 高风险工具：人工确认（pending 已在 state，resume 后直接执行，不重复决策）
    if spec.risk_level == RISK_HIGH:
        try:
            _push(
                writer,
                {
                    "type": "tool_confirm",
                    "tool": tool,
                    "args": args,
                    "risk_level": "high",
                    "description": spec.description,
                },
            )
            decision = interrupt({"type": "tool_confirm", "tool": tool, "args": args, "risk_level": "high"})
            approved = bool(decision and decision.get("approved"))
        except GraphInterrupt:
            raise  # 交还 LangGraph 处理暂停
        except Exception as e:
            logger.error(f"interrupt 失败 {tool}: {e}")
            approved = False
        if not approved:
            entry["error"] = "用户拒绝了该操作"
            trace.append(entry)
            _push(
                writer, {"type": "tool_result", "tool": tool, "success": False, "summary": "", "error": entry["error"]}
            )
            return {"loop_trace": trace, "loop_step": step, "loop_pending_tool": None, "loop_pending_args": None}

    # 执行：超时保护 + 自动重试 + 备选工具降级
    r = _execute_one(tool, args, ctx, TOOL_TIMEOUT)
    if not r["success"] and _is_retryable(r["error"]):
        # 自动重试（最多 TOOL_RETRY 次；已确认过的 high 工具不重复确认）
        for attempt in range(TOOL_RETRY):
            time.sleep(0.5)  # 短暂退避
            logger.info(f"工具 {tool} 失败，自动重试 {attempt + 1}/{TOOL_RETRY}: {r['error']}")
            _push(
                writer,
                {
                    "type": "tool_result",
                    "tool": tool,
                    "success": False,
                    "summary": "",
                    "error": f"{r['error']}（自动重试中…）",
                },
            )
            r = _execute_one(tool, args, ctx, TOOL_TIMEOUT)
            if r["success"]:
                entry["retried"] = True
                break
        # 备选工具降级：主工具仍失败时尝试 TOOL_FALLBACK 链
        if not r["success"]:
            for fb in TOOL_FALLBACK.get(tool, []):
                if not registry.get(fb):
                    continue
                logger.info(f"工具 {tool} 降级为 {fb}")
                _push(writer, {"type": "thinking", "content": f"{tool} 不可用，改用 {fb} 获取"})
                r = _execute_one(fb, args, ctx, TOOL_TIMEOUT)
                entry["fallback_to"] = fb
                if r["success"]:
                    break

    if r["success"]:
        entry["result"] = safe_truncate(str(r.get("result")), 800)
        _push(
            writer,
            {
                "type": "tool_result",
                "tool": tool,
                "success": True,
                "summary": safe_truncate(str(r.get("result")), 300),
                "error": None,
            },
        )
    else:
        entry["error"] = r.get("error") or "执行失败"
        _push(writer, {"type": "tool_result", "tool": tool, "success": False, "summary": "", "error": entry["error"]})
    from app.agent.tracing import emit as _trace_emit

    _trace_emit(
        state,
        "tool_result",
        {
            "tool": tool,
            "step": step,
            "success": bool(r.get("success")),
            "error": r.get("error"),
            "retried": entry.get("retried") or False,
            "fallback_to": entry.get("fallback_to"),
            "result_summary": (entry.get("result") or "")[:300],
        },
    )
    trace.append(entry)
    return {"loop_trace": trace, "loop_step": step, "loop_pending_tool": None, "loop_pending_args": None}
