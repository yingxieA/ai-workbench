"""生成节点：direct / rag / tool 三分支汇聚，流式输出 + 引用拼接"""

import re
import time

from app.config import settings
from app.agent.llm import achat_stream, achat_stream_vl
from app.agent.prompts import RAG_SYSTEM_PROMPT_TEMPLATE, DIRECT_SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT_TEMPLATE
from app.agent.state import AgentState
from app.agent.cancels import is_cancelled
from app.services.system_configs import get_config
from app.utils.logger import get_logger

logger = get_logger("agent_generator")


def _build_messages(state: AgentState) -> tuple[list[dict], bool]:
    """组装 messages：画像 + 会话摘要 + 历史 + 当前问题 + 分支 prompt。

    返回 (messages, has_images)——rag 分支命中图片块（resource_url）时，
    user 消息升级为多模态 content（文本 + 图片 data URL），生成端用视觉模型。
    """
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
        rag_prompt = get_config("prompt_rag_system", RAG_SYSTEM_PROMPT_TEMPLATE)
        system_prompt = rag_prompt.format(ctx_count=len(contexts), context_text=context_text)
        # 空检索兜底（对齐 DeepSeek：知识库查不到 → 如实说明 + 通用参考 + 反问，严禁编造具体条款/数字）
        if not contexts:
            system_prompt += (
                "\n\n【重要·知识库未检索到相关内容】"
                "本知识库中没有与用户问题相关的文档资料（contexts 为空）。请："
                "① 结论先行：明确告诉用户「知识库中未检索到相关资料/信息」；"
                "② 如实说明情况（联网搜索也未提供有效结果时同样适用），不得编造任何具体条款、数字、日期或公司事实；"
                "③ 可给出一般性/通用参考，但必须明确标注「以下为通用参考，并非本知识库内容」；"
                "④ 结尾向用户反问澄清（如：您是指哪家公司的制度？或希望我给出一般企业制度框架清单吗？）并给出下一步建议。"
                "整体控制在简洁、诚实、有下一步出口，不要硬答、不要空话。"
            )
        # 图片块透传：命中 image_caption 块的 resource_url → 生成时附原图（P2 图文回答）
        vision_urls = [m.get("resource_url") for m in (state.get("contexts_meta") or []) if m.get("resource_url")][:2]
    elif route == "tool":
        tool_results = state.get("tool_results") or []
        lines = []
        for i, r in enumerate(tool_results, 1):
            body = ""
            if r.get("success") and r.get("result") is not None:
                body = _fmt_result(r.get("result"))
            elif r.get("error"):
                body = r["error"]
            # 工具结果不编号：正文 [n] 只对应「引用来源列表」中的网页/文档，工具事实直接陈述，避免模型写 [1] 指向"工具卡片"
            if r.get("success"):
                lines.append(f"工具「{r.get('tool', '')}」返回：\n{body}")
            else:
                lines.append(f"工具「{r.get('tool', '')}」未能执行：{str(body)[:200]}")
        tool_results_text = "\n\n".join(lines) if lines else "（本轮未产生工具结果）"
        _src_block = _sources_block(state)
        tool_results_text += _src_block
        tool_prompt = get_config("prompt_tool_system", TOOL_SYSTEM_PROMPT_TEMPLATE)
        # 引用约束二选一（对齐 DeepSeek：无引用时干净输出，不编造 [n]）：
        # 有来源 → 只能标列表内编号；无来源 → 显式禁用 [n] 标注与文末引用列表
        if _src_block:
            _cite_rule = "\n\n【引用标注约束】正文中如需引用，只能使用「引用来源列表」中出现的编号 [n]（对应网页/文档来源）；工具直接返回的事实数据（如天气、计算结果）直接陈述，不得标注 [n]。"
        else:
            _cite_rule = "\n\n【引用标注约束】本次回答没有任何可引用的网页/文档来源（引用来源列表为空），正文**禁止**使用 [n] 上标标注，也不得在文末添加引用列表；基于工具事实直接陈述即可。"
        system_prompt = tool_prompt.format(tool_results_text=tool_results_text) + _cite_rule
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
        _src_block = _sources_block(state)
        tool_prompt = get_config("prompt_tool_system", TOOL_SYSTEM_PROMPT_TEMPLATE)
        if _src_block:
            _cite_rule = "\n\n【引用标注约束】正文中如需引用，只能使用「引用来源列表」中出现的编号 [n]（对应网页/文档来源）；工具直接返回的事实数据（如天气、计算结果）直接陈述，不得标注 [n]。"
        else:
            _cite_rule = "\n\n【引用标注约束】本次回答没有任何可引用的网页/文档来源（引用来源列表为空），正文**禁止**使用 [n] 上标标注，也不得在文末添加引用列表；基于工具事实直接陈述即可。"
        system_prompt = (
            tool_prompt.format(
                tool_results_text=f"多步推理执行轨迹：\n{loop_text}\n\n请基于以上各步骤的真实结果组织最终回答，不要编造步骤中未出现的数字或事实。"
                + _src_block
            )
            + _cite_rule
        )
    else:
        system_prompt = get_config("prompt_direct_system", DIRECT_SYSTEM_PROMPT)

    messages.insert(0, {"role": "system", "content": system_prompt})
    # 记忆块插在 system 之后
    messages[1:1] = memory_blocks

    # 多模态 user 消息（P2）：命中图片块时，图片 base64 内联进 user content
    has_images = False
    if route == "rag" and vision_urls:
        try:
            from app.services.vision import image_to_data_url

            img_parts = []
            for url in vision_urls:
                data_url = image_to_data_url(url)
                if data_url:
                    img_parts.append({"type": "image_url", "image_url": {"url": data_url}})
            if img_parts:
                has_images = True
                user_content = [{"type": "text", "text": question}] + img_parts
                messages.append({"role": "user", "content": user_content})
            else:
                messages.append({"role": "user", "content": question})
        except Exception as e:
            logger.warning(f"图片消息组装失败，降级纯文本: {e}")
            messages.append({"role": "user", "content": question})
    else:
        messages.append({"role": "user", "content": question})
    return messages, has_images


# 文末引用块起始模式（[n] 行 或「参考资料/来源」标题 + [n] 行）；仅当引用块行数 ≤16 时视为"文末引用块"（防误剥正文）
_REF_TAIL_PATS = [
    re.compile(r"\n{1,2}\[(\d+)\]", re.M),
    re.compile(r"\n{1,2}(?:参考资料|参考来源|引用来源|参考文献|来源)\s*[:：]?\n{1,2}\[(\d+)\]", re.M),
]


def _strip_fake_citations(answer: str, contexts_meta) -> str:
    """输出后引用确定性清洗（对齐 DeepSeek：无引用时干净输出，不依赖模型自觉）
    - contexts_meta 为空 → 剥除正文 [n] 上标 + 文末引用块（含「参考资料/来源」标题段）
    - contexts_meta 非空 → 仅保留来源列表内编号（防模型标错号）
    """
    if not answer:
        return answer
    valid = {int(s.get("index")) for s in (contexts_meta or []) if s.get("index")}
    if not valid:
        # ① 先剥文末引用块（定位 [n] 行或标题行，行数 ≤16 才剥；「参考资料/来源」标题行连带剥除）
        _REF_HEAD = re.compile(r"^\s*(?:参考资料|参考来源|引用来源|参考文献|来源)\s*[:：]?\s*$")
        for pat in _REF_TAIL_PATS:
            m = pat.search(answer)
            if m:
                tail = answer[m.start() :]
                if len(tail.split("\n")) <= 16:
                    head = answer[: m.start()].rstrip("\n")
                    prev_lines = head.split("\n")
                    if prev_lines and _REF_HEAD.match(prev_lines[-1]):
                        head = "\n".join(prev_lines[:-1]).rstrip("\n")
                    answer = head
                    break
        # ② 再剥正文引用上标（含相邻空白，不吞换行）
        answer = re.sub(r"[ \t]*\[(\d+)\][ \t]*", "", answer)
    else:
        # 有来源：正文只允许来源列表内编号
        answer = re.sub(r"\[(\d+)\]", lambda mm: mm.group(0) if int(mm.group(1)) in valid else "", answer)
    return answer.strip()


def _sources_block(state) -> str:
    """引用来源列表注入：编号与前端「引用来源」Drawer 完全一致，供正文标注 [[n]] 对齐"""
    sources = state.get("contexts_meta") or []
    if not sources:
        return ""
    src_lines = []
    for s in sources:
        idx = s.get("index")
        if not idx:
            continue
        title = str(s.get("title") or s.get("url") or "未命名")
        content = str(s.get("content") or "")[:150]
        url = s.get("url") or ""
        src_lines.append(f"[{idx}] {title}\n    链接: {url}\n    摘要: {content}")
    if not src_lines:
        return ""
    return (
        "\n\n【引用来源列表（编号与最终展示完全一致，正文标注 [[n]] 必须使用这里的编号，只标注实际引用的来源）】\n"
        + "\n\n".join(src_lines)
    )


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


async def generate(state: AgentState) -> AgentState:
    """生成节点：逐 token 通过 StreamWriter 推送，return 完整结果（方案 B：achat_stream 协程级真杀）"""
    # P2 取消传播：被打断（新请求/停止/断开）→ 不启动生成，直接收尾（generate 是耗时的 LLM 流式段）
    if is_cancelled(state.get("session_id", ""), state.get("epoch")):
        logger.info(f"生成被取消，跳过: session={state.get('session_id')}")
        return {"answer": "", "interrupted": True}
    _node_t0 = time.time()
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()

    # 推理模型（如 deepseek-reasoner）的 reasoning_content 流式透传 → 前端思考卡展开显示完整推理
    def _on_reasoning(piece: str) -> None:
        try:
            writer({"type": "thinking", "content": piece, "step_kind": "reasoning"})
        except Exception:
            pass

    messages, has_images = _build_messages(state)
    logger.info(f"生成开始: route={state.get('route')} has_images={has_images} msgs={len(messages)}")
    try:
        if has_images:
            # 图文场景走视觉模型；失败降级文本链（去掉图片消息），绝不阻断回答
            try:
                gen, info = await achat_stream_vl(
                    messages, session_id=state.get("session_id", ""), epoch=state.get("epoch")
                )
            except Exception as e:
                logger.warning(f"视觉模型生成失败，降级文本链: {e}")
                text_messages = [m for m in messages if not (isinstance(m.get("content"), list))]
                text_messages[-1]["content"] = state.get("question", "")
                gen, info = await achat_stream(
                    text_messages,
                    on_reasoning=_on_reasoning,
                    session_id=state.get("session_id", ""),
                    epoch=state.get("epoch"),
                )
        else:
            gen, info = await achat_stream(
                messages, on_reasoning=_on_reasoning, session_id=state.get("session_id", ""), epoch=state.get("epoch")
            )
        answer = ""
        _sid = state.get("session_id", "")
        # P0：流式分段输出审核（确定性规则，轻量正则）——每累积 _audit_chunk 字符审一次，
        # 命中立即中断生成并替换为拦截话术，未发出的 token 不再流出（违规内容零暴露）；
        # 生成完成后的全量审核仍保留作兜底（含 LLM 级语义审核）
        from app.agent.guardrails import output_audit

        _audit_streaming = settings.PREFILTER_OUTPUT_AUDIT_STREAMING
        _audit_chunk = max(50, settings.PREFILTER_OUTPUT_AUDIT_CHUNK)
        async for token in gen:
            if not token:
                continue
            # 打断检查：新请求到达（打断模式标记）→ 立即终止生成，尽快释放会话锁让新问题执行
            if is_cancelled(_sid, state.get("epoch")):
                logger.info(f"生成被新请求打断: session={_sid}")
                break
            answer += token
            # 跨过 chunk 边界时对已生成内容做确定性规则审核（正则 union 全量扫描，千字级成本 <1ms）
            if _audit_streaming and len(answer) >= _audit_chunk and len(answer) % _audit_chunk < len(token):
                _audit = output_audit(answer)
                if _audit["verdict"] == "block":
                    logger.warning(f"流式输出审核拦截: {_audit['detail']} matched={_audit.get('matched')}")
                    answer = "抱歉，该回答未通过内容安全审核，已拦截显示。"
                    try:
                        writer(
                            {
                                "type": "content_blocked",
                                "reason": _audit["detail"],
                                "matched": _audit.get("matched", ""),
                            }
                        )
                    except Exception:
                        pass
                    break
            try:
                writer({"type": "token", "content": token})
            except Exception:
                pass  # 无流式消费者时忽略
        logger.info(
            f"生成完成: route={state.get('route')}, model={info.get('model_used')}, fallback={info.get('fallback_triggered')}, "
            f"tokens={info.get('token_usage')}, latency={info.get('latency_ms')}ms"
        )

        # 输出内容审核（P2.6 安全治理）
        # ① 确定性规则：敏感词 / 违规词 / 注入回写特征 → 命中替换为拦截提示
        from app.agent.guardrails import output_audit, output_llm_audit

        audit = output_audit(answer)
        if audit["verdict"] == "block":
            logger.warning(f"输出内容审核拦截: {audit['detail']} matched={audit.get('matched')}")
            answer = "抱歉，该回答未通过内容安全审核，已拦截显示。"
            try:
                writer({"type": "content_blocked", "reason": audit["detail"], "matched": audit.get("matched", "")})
            except Exception:
                pass
        # ② LLM 级语义审核（开关控制，默认关；超时/失败降级放行不阻塞主流程）
        else:
            llm_audit = output_llm_audit(answer, state.get("user_id", ""))
            if llm_audit["verdict"] == "block":
                logger.warning(f"LLM 输出审核拦截: {llm_audit.get('reason')}")
                answer = "抱歉，该回答未通过内容安全审核，已拦截显示。"
                try:
                    writer(
                        {
                            "type": "content_blocked",
                            "reason": "output_llm",
                            "matched": "",
                            "detail": llm_audit.get("reason", ""),
                        }
                    )
                except Exception:
                    pass
        # 输出后引用清洗（对齐 DeepSeek：无来源禁 [n]，有来源只保留列表内编号）——入库/回放永远干净
        _clean = _strip_fake_citations(answer, state.get("contexts_meta"))
        if _clean != answer:
            logger.info(f"引用清洗: route={state.get('route')} 剥除违规引用标注 ({len(answer) - len(_clean)} 字符)")
            answer = _clean
        from app.agent.tracing import emit as _trace_emit

        _trace_emit(
            state,
            "generate",
            {
                "route": state.get("route", "rag"),
                "answer_len": len(answer),
                "fallback_triggered": bool(info.get("fallback_triggered")),
                "error_type": info.get("error_type"),
            },
            model=info.get("model_used"),
            latency_ms=info.get("latency_ms"),
            cost=info.get("cost"),
        )
        _trace_emit(state, "final", {"answer": answer[:300]})
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
