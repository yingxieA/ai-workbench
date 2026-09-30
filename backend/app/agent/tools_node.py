"""工具执行节点：LLM 生成参数 → jsonschema 校验 → 高风险人工确认 → 执行 → 结果回流

- 参数生成：对每个工具用 LLM 结构化输出 JSON 参数（兼容降级链所有模型，不依赖各家 function calling）
- 校验：jsonschema 拦截参数注入；校验失败不执行、结果带 error 回流
- 高风险（run_code / http_request / 外部写类工具）：interrupt 人工确认，确认后从断点继续
"""

from __future__ import annotations

import json
import re

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from app.agent.llm import chat_complete
from app.agent.state import AgentState
from app.agent.tools.registry import ToolContext, registry, RISK_HIGH
from app.agent.tools.executor import execute_tool
from app.agent.tools.validation import safe_truncate, validate_arguments
from app.utils.logger import get_logger

logger = get_logger("tool_execute_node")

ARGS_PROMPT = """你是工具参数生成器。根据用户问题与工具定义，为指定工具生成合法的 JSON 参数。

工具：{name}
用途：{description}
参数 Schema：{schema}

用户问题：{question}

要求：
1. 只输出 JSON 参数对象，不要任何其他文字（不要 ```json 包裹）
2. 参数必须符合 Schema（必填项必须有、类型正确、enum 取合法值）
3. 无法从问题推断的可选参数省略
4. 用户问题明显不需要该工具时，输出 {{"__skip__": true}}
"""


def _parse_args(text: str) -> dict:
    if not text:
        return {}
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _generate_args(name: str, spec, question: str) -> dict:
    """LLM 结构化生成工具参数（失败降级为空 dict，由校验兜底）"""
    prompt = ARGS_PROMPT.format(
        name=name,
        description=spec.description,
        schema=json.dumps(spec.parameters, ensure_ascii=False),
        question=question,
    )
    try:
        text, _ = chat_complete([{"role": "user", "content": prompt}])
        args = _parse_args(text)
        return args if isinstance(args, dict) else {}
    except Exception as e:
        logger.warning(f"参数生成失败 {name}: {e}")
        return {}


def tool_execute(state: AgentState) -> AgentState:
    """顺序执行工具：每个工具独立 参数生成→校验→(高风险中断)→执行"""
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()

    tools = state.get("tool_calls") or []
    question = state.get("question", "")
    ctx = ToolContext(user_id=state.get("user_id"), session_id=state.get("session_id"), question=question)

    results: list[dict] = []
    for name in tools:
        spec = registry.get(name)
        if spec is None:
            results.append({"success": False, "tool": name, "result": None, "error": f"工具不存在: {name}"})
            _push_result(writer, name, False, None, f"工具不存在: {name}")
            continue

        from app.agent.tracing import emit as _trace_emit

        args = _generate_args(name, spec, question)
        _trace_emit(state, "tool_call", {"tool": name, "args": args})
        if args.get("__skip__"):
            results.append({"success": False, "tool": name, "result": None, "error": "工具不适用于当前问题（已跳过）"})
            _push_result(writer, name, False, None, "跳过：当前问题不需要该工具")
            continue

        ok, err = validate_arguments(spec, args)
        if not ok:
            results.append({"success": False, "tool": name, "result": None, "error": err})
            _push_result(writer, name, False, None, err)
            logger.warning(f"工具 {name} 参数注入被拦截: {err}")
            continue

        # 高风险工具：人工确认（interrupt 保存断点，resume 后从返回值继续）
        if spec.risk_level == RISK_HIGH:
            try:
                _push(
                    writer,
                    {
                        "type": "tool_confirm",
                        "tool": name,
                        "args": args,
                        "risk_level": "high",
                        "description": spec.description,
                    },
                )
                # 首次执行：interrupt 抛 GraphInterrupt 让图暂停（保存 checkpoint）；
                # resume 后：返回用户决策值。绝不能吞掉 GraphInterrupt，否则确认不生效
                decision = interrupt({"type": "tool_confirm", "tool": name, "args": args, "risk_level": "high"})
                approved = bool(decision and decision.get("approved"))
            except GraphInterrupt:
                raise  # 交还 LangGraph 处理暂停
            except Exception as e:
                logger.error(f"interrupt 失败 {name}: {e}")
                approved = False
            if not approved:
                results.append(
                    {"success": False, "tool": name, "result": None, "error": "用户拒绝了该操作", "rejected": True}
                )
                _push_result(writer, name, False, None, "用户拒绝了该操作")
                continue

        r = execute_tool(name, args, ctx)
        results.append(r)
        _trace_emit(
            state,
            "tool_result",
            {
                "tool": name,
                "success": bool(r.get("success")),
                "error": r.get("error"),
                "result_summary": (str(r.get("result"))[:300] if r.get("success") else ""),
            },
        )
        _push_result(writer, name, r["success"], r.get("result"), r.get("error"))

    return {"tool_results": results, "tool_confirm_pending": False}


def _push(writer, payload: dict) -> None:
    try:
        writer(payload)
    except Exception:
        pass  # 无流式消费者时忽略


def _push_result(writer, name: str, success: bool, result, error: str | None) -> None:
    summary = ""
    if success:
        r = result or {}
        if isinstance(r, dict):
            summary = r.get("text") or r.get("result") or r.get("stdout") or r.get("body") or ""
            if not summary:
                # 结果全空（如 run_code 无 print 输出）→ 友好提示；否则兜底 JSON 摘要
                if all(v in (None, "", False, [], {}) for v in r.values()):
                    summary = "执行成功（无输出）"
                else:
                    summary = json.dumps(r, ensure_ascii=False)[:300]
        else:
            summary = str(r)[:300]
    _push(
        writer,
        {
            "type": "tool_result",
            "tool": name,
            "success": success,
            "summary": safe_truncate(summary or "", 600),
            "error": error,
        },
    )
