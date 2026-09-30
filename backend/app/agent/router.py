"""Super Router 节点：LLM 结构化输出意图路由（direct / rag / tool 三分支）

- direct：闲聊、通用常识、翻译、通用代码
- rag：知识库/文档事实问答（需检索私有资料）
- tool：强意图匹配，需调用工具（搜索/抓取/计算/时间/执行代码/记忆等）

工具分支策略：system prompt 注入工具清单，LLM 选择 0~N 个工具；
规则层做强意图辅助（纯计算/时间表达直接补候选），最终由 LLM 输出确认。
"""

import json
import re

from app.agent.llm import chat_complete
from app.agent.state import AgentState
from app.agent.tools.registry import registry
from app.utils.logger import get_logger

logger = get_logger("router")

_BASE_PROMPT = """你是智能问答系统的意图路由器。判断用户输入属于哪一类：

- direct：问候、闲聊、询问身份、与知识库无关的通用常识、翻译、与文档无关的通用代码片段
- rag：询问文档/知识库中的具体内容、产品规则、业务事实、技术教程等，需要检索私有资料才能准确回答
- tool：需要调用外部工具才能完成的请求——实时信息（天气/新闻/股价/时事）、精确计算、当前时间、运行代码、联网抓取内容等
- agent_loop：需要拆解成多个步骤、反复调用多个工具才能完成的复杂任务（如"帮我查今天珠海天气、算上去珠海站要多久、再对比高铁和自驾"——涉及多步、多工具、中间结果互相依赖）

【重要：本项目知识库主题】
知识库内容覆盖 AI 技术主题：FastAPI、RAG 检索、LangGraph、大模型（LLM）、Agent、模型微调、智能问答、文档管理、Embedding 等。
凡涉及上述主题的具体问题——即使看起来像"怎么开发/怎么实现"——都应走 rag，因为知识库里有权威资料可供引用。
此外，凡涉及"公司/产品/平台"的政策、规则、条款类问题（退货、换货、隐私、数据安全、考勤、员工手册、API 频率限制、服务条款等业务文档内容），也应走 rag——这类信息必须以库内文档为准，不得凭空回答。
只有明显与知识库无关的问题才走 direct、tool 或 agent_loop。

【工具选择规则】
- 用户明确要"查最新/搜一下/联网查询"且与知识库无关 → tool（web_search）
- 精确数学计算 → tool（calculator）
- 当前日期时间 → tool（get_current_time）
- 运行 Python 代码 → tool（run_code，需人工确认）
- 需要实时数据或知识库外信息，但检索类工具能解决 → tool
- 知识库内主题问题绝不因"看着像要检索"而走 tool，走 rag
- 单个问题只涉及一个工具的简单调用 → tool；涉及多个步骤、多个工具、需要逐步决策的复杂任务 → agent_loop

只输出 JSON，不要输出任何其他文字，格式如下：
{"route": "direct" 或 "rag" 或 "tool" 或 "agent_loop", "tools": ["工具名", ...], "confidence": 0.0 到 1.0 之间的小数, "detail": "分类说明"}

其中 detail 的取值：
- route 为 direct：detail 为 "chitchat"（闲聊/问候）或 "llm_common"（通用常识/翻译/通用代码）
- route 为 rag：detail 为 "knowledge_qa"（知识库问答）
- route 为 tool：detail 为 "tool_call"，tools 为要调用的工具名数组（可为空数组则退回 rag）
- route 为 agent_loop：detail 为 "multi_step"，tools 可为预估会用到的工具名数组（可空）

当前可用工具清单：
{tools_summary}

示例：
Q: 你好
A: {{"route": "direct", "tools": [], "confidence": 0.98, "detail": "chitchat"}}
Q: FastAPI 流式接口怎么开发
A: {{"route": "rag", "tools": [], "confidence": 0.95, "detail": "knowledge_qa"}}
Q: 帮我算一下 128*0.13 等于多少
A: {{"route": "tool", "tools": ["calculator"], "confidence": 0.99, "detail": "tool_call"}}
Q: 现在珠海几点了
A: {{"route": "tool", "tools": ["get_current_time"], "confidence": 0.98, "detail": "tool_call"}}
Q: 搜一下最新的 AI Agent 框架对比
A: {{"route": "tool", "tools": ["web_search"], "confidence": 0.9, "detail": "tool_call"}}
Q: 帮我规划明天去广州的行程：查天气、算日期、查高铁
A: {{"route": "agent_loop", "tools": ["date_calculator", "web_search"], "confidence": 0.85, "detail": "multi_step"}}"""

# 强意图辅助规则：命中即把候选工具塞进 prompt（最终仍由 LLM 决定）
_CALC_RE = re.compile(r"^[\d+\-*/().%\s^]+$|计算|算一下|等于多少|是多少|求值")
_TIME_RE = re.compile(
    r"现在几点|几点了|今天几号|星期几|当前时间|现在时间|今天是几月|现在的日期|时区|昨天|前天|明天|后天"
)
_HIST_DATE_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}")
_SEARCH_RE = re.compile(r"搜一下|搜一搜|搜索|查一下最新|联网查|最新消息|最近新闻|今天天气|天气预报")


def _rule_hint(question: str) -> str:
    hints = []
    if _CALC_RE.search(question):
        hints.append("calculator")
    if _HIST_DATE_RE.search(question):
        hints.append("date_calculator")
    if _TIME_RE.search(question):
        hints.append("get_current_time")
    if _SEARCH_RE.search(question):
        hints.append("web_search")
    if hints:
        # 去重保序
        seen = set()
        unique = [h for h in hints if not (h in seen or seen.add(h))]
        return "【规则提示】用户问题疑似包含以下意图，请在 tools 中优先考虑：" + "、".join(unique)
    return ""


def _forced_tool(question: str) -> str:
    """强意图工具：LLM 未选工具时规则层强制补上（不退 rag，避免时间/计算/搜索类无依据编造）
    优先级：历史日期推算 > 计算 > 当前时间 > 搜索"""
    if _HIST_DATE_RE.search(question):
        return "date_calculator"
    if _CALC_RE.search(question):
        return "calculator"
    if _TIME_RE.search(question):
        return "get_current_time"
    if _SEARCH_RE.search(question):
        return "web_search"
    return ""


def _safe_parse(text: str) -> dict:
    """容错解析：去 ```json 包裹、提取第一个 { } JSON 块"""
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


def super_router(state: AgentState) -> AgentState:
    """意图识别节点：输出 route + tools + confidence + detail，并推送路由事件"""
    writer = None
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:
        pass  # 图外调用（单测/脚本）时无 StreamWriter，静默降级

    question = state.get("question", "")
    hint = _rule_hint(question)
    # 用 replace 而非 format：prompt 内大量 JSON 示例含单花括号，format 会误判占位符
    system_prompt = _BASE_PROMPT.replace("{tools_summary}", registry.summary())
    if hint:
        system_prompt += "\n\n" + hint

    route, confidence, detail = "rag", 0.0, "knowledge_qa"
    tools: list[str] = []
    info = {"model_used": None}
    text: str | None = None
    try:
        text, info = chat_complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ]
        )
        parsed = _safe_parse(text)
        route = parsed.get("route", "rag")
        if route not in ("direct", "rag", "tool", "agent_loop"):
            route = "rag"  # 非法值兜底
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        detail = parsed.get("detail", "")
        if route == "direct" and detail not in ("chitchat", "llm_common"):
            detail = "llm_common"
        if route == "rag" and detail != "knowledge_qa":
            detail = "knowledge_qa"
        if route == "tool":
            detail = "tool_call"
            # 工具名校验：只保留工具池中存在的
            raw_tools = parsed.get("tools") or []
            if isinstance(raw_tools, list):
                tools = [t for t in raw_tools if isinstance(t, str) and registry.get(t)]
        if route == "agent_loop":
            detail = "multi_step"
            raw_tools = parsed.get("tools") or []
            if isinstance(raw_tools, list):
                tools = [t for t in raw_tools if isinstance(t, str) and registry.get(t)]
        # 低置信度兜底：不确定时宁可走 RAG，保证有据可依
        if confidence < 0.6:
            logger.info(f"低置信度 {confidence:.2f}，兜底走 rag")
            route, tools = "rag", []
            detail = "knowledge_qa"
        # tool/agent_loop 分支但没有合法工具：tool 退回 rag；agent_loop 仍可进（图内循环会自行决策，无需 Router 预选工具）
        if route == "tool" and not tools:
            forced = _forced_tool(question)
            if forced:
                tools = [forced]
                logger.info(f"tool 分支规则强干预补工具: {forced}（不退 rag）")
            else:
                logger.info("tool 分支未选择合法工具，退回 rag")
                route, detail = "rag", "knowledge_qa"
        logger.info(
            f"路由结果: route={route}, tools={tools}, confidence={confidence:.2f}, detail={detail}, model={info.get('model_used')}"
        )
    except Exception as e:
        logger.error(f"意图识别失败，兜底走 rag: {e}")
        route, confidence, detail, tools = "rag", 0.0, "knowledge_qa", []

    result = {
        "route": route,
        "route_detail": detail,
        "confidence": round(confidence, 2),
        "router_model": info.get("model_used"),
        "tool_calls": tools,
    }
    from app.agent.tracing import emit as _trace_emit

    _trace_emit(
        state,
        "route",
        {"route": route, "detail": detail, "confidence": round(confidence, 2), "tools": tools},
        model=info.get("model_used"),
    )
    _trace_emit(state, "route_raw", {"raw": (text or "")[:2000], "route": route}, model=info.get("model_used"))
    try:
        writer({"type": "route", **result})
        writer({"type": "route_raw", "raw": (text or "")[:2000], "route": route, "model": info.get("model_used")})
    except Exception:
        pass  # 无流式消费者时忽略
    return result
