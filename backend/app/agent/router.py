"""Super Router 节点：LLM 结构化输出意图路由（direct / rag / tool 三分支）

- direct：闲聊、通用常识、翻译、通用代码
- rag：知识库/文档事实问答（需检索私有资料）
- tool：强意图匹配，需调用工具（搜索/抓取/计算/时间/执行代码/记忆等）

工具分支策略：system prompt 注入工具清单，LLM 选择 0~N 个工具；
规则层做强意图辅助（纯计算/时间表达直接补候选），最终由 LLM 输出确认。
"""

import json
import re
import time

from app.agent.llm import achat_complete
from app.agent.state import AgentState
from app.agent.tools.registry import registry
from app.services.system_configs import get_config
from app.agent.cancels import is_cancelled
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
- 涉及"附近/周边/哪里有XX"且目标工具需要查找型参数（经纬度、坐标、POI ID）时，必须选 agent_loop：先调地点解析/地理检索工具（text_search/geo）拿坐标，再调周边检索工具（如 around_search），**禁止单步 tool 用地名直接填坐标类参数**（会导致"参数无效"）

只输出 JSON，不要输出任何其他文字，格式如下：
{"route": "direct" 或 "rag" 或 "tool" 或 "agent_loop", "tools": ["工具名", ...], "confidence": 0.0 到 1.0 之间的小数, "detail": "分类说明", "reasoning": "一句话说明为什么这样选", "resolved_question": "可选字段——仅当用户消息是代词/省略句需要补全时，输出补全后的完整问题原文；独立完整问题不输出该字段"}

其中 detail 的取值：
- route 为 direct：detail 为 "chitchat"（闲聊/问候）或 "llm_common"（通用常识/翻译/通用代码）
- route 为 rag：detail 为 "knowledge_qa"（知识库问答）
- route 为 tool：detail 为 "tool_call"，tools 为要调用的工具名数组（可为空数组则退回 rag）
- route 为 agent_loop：detail 为 "multi_step"，tools 可为预估会用到的工具名数组（可空）
- reasoning：route 为 tool 或 agent_loop 时必须填写，用一句话说明"用户需要什么、为什么选这些工具、计划查什么"（如：用户想了解DeepSeek的全面信息，需要联网搜索其定义、背景、技术特点等多个方面）；direct/rag 可为空字符串

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
A: {{"route": "tool", "tools": ["web_search"], "confidence": 0.9, "detail": "tool_call", "reasoning": "用户要了解最新行业动态，需要联网搜索对比信息"}}
Q: 帮我规划明天去广州的行程：查天气、算日期、查高铁
A: {{"route": "agent_loop", "tools": ["date_calculator", "web_search"], "confidence": 0.85, "detail": "multi_step"}}"""

# 强意图辅助规则：命中即把候选工具塞进 prompt（最终仍由 LLM 决定）
_CALC_RE = re.compile(r"^[\d+\-*/().%\s^]+$|计算|算一下|等于多少|是多少|求值")
_TIME_RE = re.compile(
    r"现在几点|几点了|今天几号|星期几|当前时间|现在时间|今天是几月|现在的日期|时区|昨天|前天|明天|后天"
)
_HIST_DATE_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}")
_SEARCH_RE = re.compile(r"搜一下|搜一搜|搜索|查一下最新|联网查|最新消息|最近新闻|今天天气|天气预报")
# 知识库专属信号：命中这些主题说明问题指向内部知识库（员工手册/售后政策/API文档等），即使联网开关打开也走 rag
# P1.5: 词表配置化（system_configs.router_kb_signals，逗号分隔），失败回退默认
_DEFAULT_KB_SIGNALS = (
    "售后,退货,换货,政策,员工手册,考勤,请假,报销,离职,入职,制度,规范,流程,协议,合同,"
    "API文档,接口文档,开发文档,使用说明,操作指南,我们公司,我们平台,本平台,公司规定,公司的"
)
_KB_SIGNAL_RE = re.compile("|".join(_DEFAULT_KB_SIGNALS.split(",")))

# 代指信号：无主语问句（"再详细/这个/它/上面"等）——必须结合历史消解，禁止按闲聊处理
# P2 加固：增加无主语疑问模式（"会…吗/能…吗/是…吗/还…吗"，如"会下雨吗？"承接上文天气），
# 让 router 代指消解显式覆盖此类追问（{0,8} 限制中间成分长度，避免误伤长句）
_ANAPHORA_RE = re.compile(
    r"再详细|详细解释|展开说说|具体说下|这个|那个|它|上面|前面|刚才|之前|上文|上述|以上|这些|那些|接着说|继续|是什么意思|"
    r"会[^，。！？]{0,8}吗|能[^，。！？]{0,8}吗|是[^，。！？]{0,8}吗|还[^，。！？]{0,8}吗"
)  # P0.3: 移除裸"是什么"（避免误判"XX是什么"独立问题为代指；"是什么意思"更像指代，保留）

# P0.9 话题延续（省略句补全）：最新消息是短名词句且无谓词/疑问词（如"珠海第四小学"）
# → 真实意图必须结合上一条用户消息补全（上句"找篮球场"+本句"珠海第四小学"→"找珠海第四小学附近的篮球场"）
# 含疑问词/动词的消息由 _ANAPHORA_RE 或正常路由承接，不在此列
_OMISSION_STOP = re.compile(
    r"(?:吗|呢|嘛|怎么|什么|如何|为啥|哪里|在哪|多少|几号|几点|哪些|哪个|咋样|怎样|干什么|干嘛|有没有|是不是|会不会|能不能|要不要)"
)


def _is_omission_question(question: str) -> bool:
    """省略句启发式：长度 <= 12 且不含谓词/疑问词 → 视为对上文的补充（名词句/地名句）"""
    q = (question or "").strip()
    if not q or len(q) > 12:
        return False
    if _OMISSION_STOP.search(q):
        return False  # 含疑问词/动词，本身意图较完整
    return True


def _load_intent_patterns() -> dict:
    """强意图正则读取层：配置 JSON {calc,time,hist_date,search} 优先，失败回退默认"""
    try:
        from app.services.system_configs import get_config
        import json as _json

        raw = get_config("router_intent_patterns", "")
        if raw:
            d = _json.loads(raw)
            if isinstance(d, dict):
                return {
                    "calc": re.compile(str(d["calc"]), re.IGNORECASE),
                    "time": re.compile(str(d["time"]), re.IGNORECASE),
                    "hist_date": re.compile(str(d["hist_date"]), re.IGNORECASE),
                    "search": re.compile(str(d["search"]), re.IGNORECASE),
                }
    except Exception:
        pass
    return {"calc": _CALC_RE, "time": _TIME_RE, "hist_date": _HIST_DATE_RE, "search": _SEARCH_RE}


def _confidence_threshold() -> float:
    """路由置信度阈值读取层：配置优先（0~1），失败回退 0.6"""
    try:
        from app.services.system_configs import get_config

        raw = get_config("router_confidence_threshold", "")
        if raw:
            v = float(raw)
            if 0.0 <= v <= 1.0:
                return v
    except Exception:
        pass
    return 0.6


def _load_calibration() -> list[tuple[float, float, float]]:
    """置信度离线校准表读取层：配置 router_confidence_calibration（格式 "lo-hi:adj,..."，如 "0.7-1:0.85,0.4-0.7:0.5"）
    由 scripts/eval/calibrate_confidence.py 基于真实样本生成；无配置返回空（不做校准）"""
    try:
        from app.services.system_configs import get_config

        raw = get_config("router_confidence_calibration", "")
        if not raw:
            return []
        out = []
        for part in raw.split(","):
            part = part.strip()
            if ":" not in part:
                continue
            rng, adj = part.split(":", 1)
            lo_s, hi_s = rng.split("-", 1)
            lo, hi, a = float(lo_s), float(hi_s), float(adj)
            out.append((lo, hi, a))
        return out
    except Exception:
        return []


def _calibrated_confidence(conf: float) -> float:
    """按校准表分桶映射 LLM 自报置信度；无表/未命中分桶则原样返回"""
    for lo, hi, adj in _load_calibration():
        if lo <= conf < hi:
            return adj
    return conf


def _rule_hint(question: str) -> str:
    hints = []
    _pats = _load_intent_patterns()
    if _pats["calc"].search(question):
        hints.append("calculator")
    if _pats["hist_date"].search(question):
        hints.append("date_calculator")
    if _pats["time"].search(question):
        hints.append("get_current_time")
    if _pats["search"].search(question):
        hints.append("web_search")
    if hints:
        # 去重保序
        seen = set()
        unique = [h for h in hints if not (h in seen or seen.add(h))]
        return "【规则提示】用户问题疑似包含以下意图，请在 tools 中优先考虑：" + "、".join(unique)
    return ""


def _load_forced_order() -> list[str]:
    """强意图工具优先级读取层：配置 router_forced_tool_order（逗号分隔）优先，失败回退默认"""
    default = ["date_calculator", "calculator", "get_current_time", "web_search"]
    try:
        from app.services.system_configs import get_config

        raw = get_config("router_forced_tool_order", "")
        if raw:
            order = [w.strip() for w in raw.split(",") if w.strip()]
            if order:
                return order
    except Exception:
        pass
    return default


def _forced_tool(question: str) -> str:
    """强意图工具：LLM 未选工具时规则层强制补上（不退 rag，避免时间/计算/搜索类无依据编造）
    优先级：配置化（router_forced_tool_order），默认 历史日期推算 > 计算 > 当前时间 > 搜索"""
    _pats = _load_intent_patterns()
    _name2pat = {
        "date_calculator": _pats["hist_date"],
        "calculator": _pats["calc"],
        "get_current_time": _pats["time"],
        "web_search": _pats["search"],
    }
    for name in _load_forced_order():
        pat = _name2pat.get(name)
        if pat and pat.search(question):
            return name
    return ""


def _load_kb_signals() -> re.Pattern:
    """知识库信号词表读取层：配置 router_kb_signals（逗号分隔）优先，失败回退默认"""
    try:
        from app.services.system_configs import get_config

        raw = get_config("router_kb_signals", "")
        if raw:
            words = [w.strip() for w in raw.split(",") if w.strip()]
            if words:
                return re.compile("|".join(words))
    except Exception:
        pass
    return _KB_SIGNAL_RE


def _kb_signal(question: str) -> bool:
    """问题是否命中知识库专属信号（内部制度/文档/公司相关内容）"""
    return bool(_load_kb_signals().search(question))


def _fallback_route(question: str, web_on: bool) -> tuple[str, str, list[str]]:
    """低置信/异常兜底分流（P0.2）：不再一刀切 rag——
    知识库专属问题 → rag；联网搜索开 → tool(web_search)；否则 direct(LLM 直接答)"""
    if _kb_signal(question):
        return "rag", "knowledge_qa", []
    if web_on and registry.get("web_search"):
        return "tool", "tool_call", ["web_search"]
    return "direct", "llm_common", []


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


async def super_router(state: AgentState) -> AgentState:
    """意图识别节点：输出 route + tools + confidence + detail，并推送路由事件"""
    # P0 取消传播：新请求到达（打断模式标记）→ 立即结束意图识别，尽快让位新请求
    if is_cancelled(state.get("session_id", ""), state.get("epoch")):
        logger.info(f"意图识别被新请求打断: session={state.get('session_id')}")
        return {
            "route": "direct",
            "confidence": 0.0,
            "route_detail": "interrupted",
            "tool_calls": [],
            "interrupted": True,
        }
    _node_t0 = time.time()
    writer = None
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:
        pass  # 图外调用（单测/脚本）时无 StreamWriter，静默降级

    question = state.get("question", "")
    options = state.get("options") or {}
    _web_on = bool(options.get("web_search", True))
    _deep_on = bool(options.get("deep_think", True))
    hint = _rule_hint(question)
    logger.info(f"意图识别开始: q={question[:40]} web_on={_web_on} deep_on={_deep_on}")
    # 用 replace 而非 format：prompt 内大量 JSON 示例含单花括号，format 会误判占位符
    # 路由 prompt 已配置化：system_configs.prompt_super_router 热更新覆盖默认 _BASE_PROMPT
    base_prompt = get_config("prompt_super_router", _BASE_PROMPT)
    system_prompt = base_prompt.replace("{tools_summary}", registry.summary())
    if hint:
        # 联网开关关闭时：去掉 web_search 规则提示
        if not _web_on:
            hint = re.sub(r"[、]?web_search", "", hint).rstrip("、").rstrip("：").strip()
        if hint:
            system_prompt += "\n\n" + hint
    if _web_on:
        # 联网搜索开关打开：强分流提示（实时/时效/知识库外问题优先联网，别因"知识库可能相关"走 rag）
        system_prompt += "\n\n【用户设置】已开启联网搜索：实时信息、最新资讯、知识库未覆盖的主题、时效性问题、需要外部事实的问题，必须优先选择 route=tool + web_search；不要仅因知识库可能相关而走 rag。纯闲聊（问候/身份/翻译/代码/常识）仍走 direct。"
    else:
        system_prompt += (
            "\n\n【用户设置】未开启联网搜索：禁止选择 web_search 工具，也不要把问题路由到需要实时联网的分支。"
        )
    if not _deep_on:
        system_prompt += "\n\n【用户设置】未开启深度思考：禁止路由到 agent_loop，复杂多步问题直接给出简洁答案。"
    # 代指消解（P2.7）+ 话题延续（P0.9）：无主语问句/省略句必须结合最近历史，禁止按闲聊处理
    _anaphora_hit = bool(_ANAPHORA_RE.search(question))
    _omission_hit = _is_omission_question(question)
    if _anaphora_hit or _omission_hit:
        _hist = state.get("history") or []
        _recent = _hist[-6:]  # 最近 6 条（含上一轮问答）
        if _recent:
            _hist_text = "\n".join(
                f"{'用户' if m.get('role') == 'user' else '助手'}: {str(m.get('content', ''))[:300]}" for m in _recent
            )
            if _omission_hit:
                system_prompt += (
                    "\n\n【重要·话题延续】用户最新消息是省略句/短句（如“珠海第四小学”“酒店有点贵”“推荐价位低一点的”“你给我推荐”），"
                    "真实意图必须结合上一条用户消息补全（示例：上句“珠海这附近有哪些篮球场”+本句“珠海第四小学” → 完整意图=“找珠海第四小学附近的篮球场”；"
                    "上句“帮我规划珠海两日游酒店”+本句“酒店有点贵” → 完整意图=“我嫌之前推荐的酒店贵，想换便宜些的”）。"
                    "请先阅读最近历史补全完整意图，再决定路由；**禁止把省略句按字面独立路由**（如把“酒店有点贵”路由为闲聊问候），"
                    "**禁止路由为 direct/chitchat 闲聊**；结合历史可补全意图时，优先路由 rag/tool/web_search（联网开时）获取事实。"
                    "若结合历史后仍无法确定意图，路由为 rag 并反问澄清。"
                    "**必须在输出 JSON 的 resolved_question 字段中给出补全后的完整问题原文**（供下游检索/工具/生成统一使用）；"
                    "若实在无法补全，至少按“回应上文”的方向自然作答，不要按全新独立问题处理。\n最近历史：\n"
                    + _hist_text
                )
                logger.info(f"话题延续(省略句补全): q={question[:20]} 注入最近 {len(_recent)} 条历史")
            else:
                system_prompt += (
                    "\n\n【重要·代词消解】用户问题包含指代性表述（再详细/这个/它/上面/继续等），指代对象必须在最近历史中查找。"
                    "请先阅读最近历史判断指代对象，再决定路由；**禁止路由为 direct/chitchat（问候闲聊）**。"
                    "若存在指代对象，**必须在输出 JSON 的 resolved_question 字段中给出消解后的完整问题原文**。\n最近历史：\n"
                    + _hist_text
                )
                logger.info(f"代指消解: q={question[:20]} 注入最近 {len(_recent)} 条历史")
        else:
            # 无历史可消解 → 明确反问澄清，而非闲聊或瞎猜
            system_prompt += "\n\n【重要】用户问题含指代表述但无历史可查，请路由为 rag 并按「请澄清指代对象」的方向回答，禁止按问候闲聊处理。"

    route, confidence, detail = "rag", 0.0, "knowledge_qa"
    tools: list[str] = []
    info = {"model_used": None}
    text: str | None = None
    _resolved_flag = ""  # P0.1: 代指/省略句补全后的完整问题（写回 state.question 供下游统一使用）
    parsed: dict = {}  # LLM JSON 解析结果（try 外初始化：异常兜底路径后仍可安全引用）
    try:
        text, info = await achat_complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            sid=state.get("session_id", ""),
            epoch=state.get("epoch"),
        )
        parsed = _safe_parse(text)
        route = parsed.get("route", "rag")
        if route not in ("direct", "rag", "tool", "agent_loop"):
            route = "rag"  # 非法值兜底
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        # P0.1: 代指/省略句命中时，LLM 输出补全后的问题 → 写回局部 question，下游检索/工具/生成统一使用
        if _anaphora_hit or _omission_hit:
            _resolved = str(parsed.get("resolved_question") or "").strip()
            if _resolved and _resolved != question:
                logger.info(f"resolved_question: q={question[:20]} -> {_resolved[:40]}")
                question = _resolved
                _resolved_flag = _resolved
            elif not _resolved and route == "direct":
                # 兜底：LLM 未补全（判为闲聊）但实为话题延续 → 用最近一条用户消息拼接，保证生成端看到上文
                _prev_q = next(
                    (
                        str(m.get("content", "")).strip()
                        for m in reversed(_recent)
                        if m.get("role") == "user"
                        and str(m.get("content", "")).strip()
                        and str(m.get("content", "")).strip() != question
                    ),
                    None,
                )
                if _prev_q:
                    question = f"【继续上文】{_prev_q[:100]} —— 用户接着说：{question}"
                    _resolved_flag = question
                    logger.info(f"省略句兜底补全(LLM未输出resolved_question): {question[:50]}")
        # P1.7: 离线校准映射（无校准表则原样）
        confidence = _calibrated_confidence(confidence)
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
        # 前端开关过滤：未开启联网搜索 → 移除联网类工具（web_search/web_fetch/http_request）；未开启深度思考 → agent_loop 降级 rag
        if not _web_on:
            _net = [t for t in tools if t in ("web_search", "web_fetch", "http_request")]
            if _net:
                tools = [t for t in tools if t not in _net]
                logger.info(f"联网搜索开关关闭，移除联网工具: {_net}")
        if not _deep_on and route == "agent_loop":
            # P1.8: 深度思考关闭时 agent_loop 不再降 rag（多步工具任务降 rag 会答非所问）——
            # 联网开 → 单步 web_search；关 → direct 直接答
            if _web_on and registry.get("web_search"):
                route, detail, tools = "tool", "tool_call", ["web_search"]
                logger.info("深度思考开关关闭，agent_loop 降级 tool(web_search)")
            else:
                route, detail, tools = "direct", "llm_common", []
                logger.info("深度思考开关关闭，agent_loop 降级 direct")
        # 联网搜索开关打开 + 当前是 rag：若非知识库专属问题（内部制度/文档主题）强制切 web_search
        # 用户打开开关 = 明确要联网答案，规则层直接分流，不让 LLM 因"知识库可能相关"走 rag
        if _web_on and route == "rag":
            if not _kb_signal(question) and registry.get("web_search"):
                route, detail, tools = "tool", "tool_call", ["web_search"]
                confidence = max(confidence, 0.9)
                logger.info(f"联网搜索开关打开，非知识库专属问题强制切 web_search: {question[:30]}")
        # 低置信度兜底（P0.2）：按主题分流——知识库专属 → rag；联网开 → web_search；否则 direct
        if confidence < _confidence_threshold():
            route, detail, tools = _fallback_route(question, _web_on)
            logger.info(f"低置信度 {confidence:.2f}，兜底分流 route={route}")
        # tool/agent_loop 分支但没有合法工具：tool 退回 rag；agent_loop 仍可进（图内循环会自行决策，无需 Router 预选工具）
        if route == "tool" and not tools:
            forced = _forced_tool(question)
            if forced == "web_search" and not _web_on:
                forced = ""
                logger.info("联网搜索开关关闭，强干预跳过 web_search")
            if forced:
                tools = [forced]
                logger.info(f"tool 分支规则强干预补工具: {forced}（不退 rag）")
            else:
                logger.info("tool 分支未选择合法工具，退回 rag")
                route, detail = "rag", "knowledge_qa"
        logger.info(
            f"意图识别完成: route={route}, tools={tools}, confidence={confidence:.2f}, detail={detail}, "
            f"model={info.get('model_used')}, latency={int((time.time() - _node_t0) * 1000)}ms"
        )
    except Exception as e:
        logger.error(f"意图识别失败，兜底分流: {e}")
        route, detail, tools = _fallback_route(question, _web_on)
        confidence = 0.0

    result = {
        "route": route,
        "route_detail": detail,
        "confidence": round(confidence, 2),
        "router_model": (info or {}).get("model_used"),
        "tool_calls": tools,
    }
    if _resolved_flag:
        # P0.1: 覆盖 state.question——下游检索/工具参数/agent_loop 决策/生成统一用补全后问题
        result["question_resolved"] = _resolved_flag
        result["question"] = _resolved_flag
    from app.agent.tracing import emit as _trace_emit

    _trace_emit(
        state,
        "route",
        {"route": route, "detail": detail, "confidence": round(confidence, 2), "tools": tools},
        model=(info or {}).get("model_used"),
    )
    _trace_emit(state, "route_raw", {"raw": (text or "")[:2000], "route": route}, model=info.get("model_used"))
    # route=tool/agent_loop：透传模型真实推理（对齐 DeepSeek「用户想知道…我需要…我将…」的思考叙事）
    _reasoning = str((parsed or {}).get("reasoning", "") or "").strip()
    if route in ("tool", "agent_loop"):
        if not _reasoning:
            # 强分流/无 reasoning：推一句真实动作规划（该问题需要实时/外部信息 → 联网搜索）
            _reasoning = "该问题需要最新或外部信息，我将联网搜索获取资料后回答"
        try:
            writer({"type": "thinking", "content": _reasoning})
        except Exception:
            pass
    try:
        writer({"type": "route", **result})
        writer({"type": "route_raw", "raw": (text or "")[:2000], "route": route, "model": info.get("model_used")})
    except Exception:
        pass  # 无流式消费者时忽略
    return result
