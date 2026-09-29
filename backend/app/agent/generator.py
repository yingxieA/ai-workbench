"""生成节点：direct / rag / tool 三分支汇聚，流式输出 + 引用拼接"""

from app.agent.llm import chat_stream
from app.agent.prompts import RAG_SYSTEM_PROMPT_TEMPLATE, DIRECT_SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT_TEMPLATE
from app.agent.state import AgentState
from app.utils.logger import get_logger

logger = get_logger("agent_generator")


def _build_messages(state: AgentState) -> list[dict]:
    """组装 messages：画像 + 会话摘要 + 历史 + 当前问题 + 分支 prompt"""
    question = state.get("question", "")
    history = state.get("history") or []
    route = state.get("route", "rag")
    summary = (state.get("summary") or "").strip()
    profile = (state.get("profile") or "").strip()

    # P2.5 记忆块：画像 + 摘要放在 system prompt 之后、历史之前（模型先"记住"再"看"对话）
    memory_blocks: list[dict] = []
    if profile:
        memory_blocks.append({"role": "system", "content": profile})
    if summary:
        memory_blocks.append(
            {"role": "system", "content": f"【历史会话摘要（更早的对话，供参考，不要当作当前事实来源）】\n{summary}"}
        )

    messages = [{"role": h["role"], "content": h["content"]} for h in history]

    if route == "rag":
        contexts = state.get("contexts") or []
        context_text = "\n\n".join([f"[[{i + 1}]] {c}" for i, c in enumerate(contexts)])
        system_prompt = RAG_SYSTEM_PROMPT_TEMPLATE.format(ctx_count=len(contexts), context_text=context_text)
    elif route == "tool":
        tool_results = state.get("tool_results") or []
        lines = []
        for i, r in enumerate(tool_results, 1):
            status = "成功" if r.get("success") else "失败"
            body = ""
            if r.get("success") and r.get("result") is not None:
                body = _fmt_result(r.get("result"))
            elif r.get("error"):
                body = r["error"]
            lines.append(f"[工具 {i}] {r.get('tool', '')}（{status}）\n{body}")
        tool_results_text = "\n\n".join(lines) if lines else "（本轮未产生工具结果）"
        system_prompt = TOOL_SYSTEM_PROMPT_TEMPLATE.format(tool_results_text=tool_results_text)
    elif route == "agent_loop":
        # 多步推理：把完整执行轨迹拼进 prompt，让最终回答基于工具事实而非臆测
        trace = state.get("loop_trace") or []
        lines = []
        for t in trace:
            if t.get("error"):
                lines.append(f"步骤{t.get('step')} [失败] {t.get('tool')}: {t['error']}")
            else:
                lines.append(f"步骤{t.get('step')} [成功] {t.get('tool')}: {_fmt_result(t.get('result'))}")
        loop_text = "\n".join(lines) if lines else "（多步任务未产生工具结果，直接回答）"
        system_prompt = TOOL_SYSTEM_PROMPT_TEMPLATE.format(
            tool_results_text=f"多步推理执行轨迹：\n{loop_text}\n\n请基于以上各步骤的真实结果组织最终回答，不要编造步骤中未出现的数字或事实。"
        )
    else:
        system_prompt = DIRECT_SYSTEM_PROMPT

    messages.insert(0, {"role": "system", "content": system_prompt})
    # 记忆块插在 system 之后
    messages[1:1] = memory_blocks
    messages.append({"role": "user", "content": question})
    return messages


def _fmt_result(result) -> str:
    if isinstance(result, dict):
        # 优先取工具返回的"文本类"字段，兜底 JSON
        for k in ("text", "result", "stdout", "body", "content", "answer"):
            if result.get(k) is not None:
                return str(result[k])
        import json

        try:
            return json.dumps(result, ensure_ascii=False)[:2000]
        except Exception:
            return str(result)[:2000]
    return str(result)[:2000]


def generate(state: AgentState) -> AgentState:
    """生成节点：逐 token 通过 StreamWriter 推送，return 完整结果"""
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()
    messages = _build_messages(state)
    try:
        gen, info = chat_stream(messages)
        answer = ""
        for token in gen:
            if not token:
                continue
            answer += token
            try:
                writer({"type": "token", "content": token})
            except Exception:
                pass  # 无流式消费者时忽略
        logger.info(
            f"生成完成: route={state.get('route')}, model={info.get('model_used')}, fallback={info.get('fallback_triggered')}, "
            f"tokens={info.get('token_usage')}, latency={info.get('latency_ms')}ms"
        )
        return {
            "answer": answer,
            "model_used": info.get("model_used"),
            "fallback_info": {
                "fallback_triggered": bool(info.get("fallback_triggered")),
                "error_type": info.get("error_type"),
                "from_model": info.get("from_model"),
                "to_model": info.get("to_model"),
            },
            "token_usage": info.get("token_usage") or 0,
            "latency_ms": info.get("latency_ms") or 0,
            "cost": info.get("cost") or 0,
        }
    except Exception as e:
        logger.error(f"生成失败: {e}")
        fallback_text = "抱歉，服务暂时不可用，请稍后再试。"
        try:
            writer({"type": "token", "content": fallback_text})
        except Exception:
            pass
        return {
            "answer": fallback_text,
            "model_used": None,
            "fallback_info": {
                "fallback_triggered": True,
                "error_type": "generation_error",
                "from_model": None,
                "to_model": None,
            },
            "token_usage": 0,
            "latency_ms": 0,
        }
