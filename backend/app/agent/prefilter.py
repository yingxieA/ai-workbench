"""P2.3 前置过滤层（图外执行，进入 LangGraph 前）

四道关卡（全确定性，0 模型调用）：
    ① 敏感词过滤   —— AC 自动机思路（正则 union），命中高危 → 拒绝 + 审计
    ② 长度校验     —— 空 / 过短（指令白名单续上轮）/ 过长 → 追问或提示
    ③ 垃圾流量过滤 —— 用户维度频率（Redis，进程内兜底）+ 重复 + URL 堆砌
    ③.5 Prompt 注入防护 —— guardrails.injection_check（独立安全层：直接注入 / 泄露探测）
    ④ 规则匹配     —— DB 话术库（intent_rules）：精确 → 正则 → 关键词，命中直接应答

三层递进：
    L1 规则层（0 成本，确定性）→ L2 轻量模型（qwen-turbo，条件触发闲聊分类，
    默认关闭可配置）→ L3 LLM 兜底（放行进入 super_router 图内完整意图路由）

拦截 / 直接应答均写审计（AuditLog action=prefilter_*），放行记录 latency。
"""

from __future__ import annotations

import re
import time
import threading
import json
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("prefilter")

# ---------------------------------------------------------------------------
# Redis（限流计数用；不可用时降级进程内计数，与 rule_keywords 同款容错）
# ---------------------------------------------------------------------------
try:
    import redis as redis_lib

    _redis = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis.ping()
    REDIS_OK = True
except Exception as e:
    logger.warning(f"Redis 不可用，限流降级为进程内计数: {e}")
    _redis = None
    REDIS_OK = False

# 进程内限流计数兜底 {key: (window_start, count)}
_mem_rate: dict[str, list] = {}
_mem_rate_lock = threading.Lock()

# ---------------------------------------------------------------------------
# ① 敏感词
# ---------------------------------------------------------------------------
# 上下文白名单：句子包含这些长词时跳过拦截（防误伤正常表达）
SENSITIVE_WHITELIST = ("打死我也不说", "黄色网站", "毒品交易")
_CONTINUE_WORDS = {"继续", "继续回答", "接着说", "接上", "1", "yes", "y", "再详细点", "详细说说", "展开说说"}

# Prompt 注入特征（③ 垃圾流量过滤的一部分）
_INJECTION_PATTERNS = (
    r"忽略.{0,12}(指令|提示|规则|要求|prompt)",
    r"无视.{0,12}(指令|提示|规则|要求|prompt)",
    r"system\s*prompt",
    r"系统提示词?|系统指令|系统规则",
    r"输出.{0,8}(系统|内部|隐藏|开发者).{0,6}(提示词?|指令|规则|prompt)",
    r"开发者(指令|提示|模式)",
    r"越狱|jailbreak",
    r"base64\s*(解码|decode)?",
    r"扮演(上帝|系统管理员|开发者)",
    r"你(现在|必须|请)?(假装|扮演).{0,20}(不受|无视|绕过).{0,10}(限制|规则)",
)
_COMPILED_INJECTIONS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
# 全角 → 半角 + 空白归一（等长映射：，。！？（）【】“”‘’；：、）
_FULL_TO_HALF = str.maketrans("，。！？（）【】“”‘’；：、", ",.!?()[]\"\"'';:,")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """变体归一化：小写 → 全角转半角 → 去空白（谐音/拆字等复杂变体留待词库扩展）"""
    t = text.lower()
    t = t.translate(_FULL_TO_HALF)
    return _WS_RE.sub("", t)


def sensitive_check(question: str) -> Optional[dict]:
    """命中返回 {keyword, level}，否则 None。词表来自 rule_keywords(category=sensitive)。"""
    from app.services.rule_keywords import get_keywords

    for w in SENSITIVE_WHITELIST:
        if w in question:
            return None
    words = get_keywords("sensitive")
    if not words:
        return None
    # 正则 union：多模式一次遍历（词表规模可控；超大规模生产可换 pyahocorasick AC 自动机）
    nq = _normalize(question)
    n_words = [_normalize(w["keyword"]) for w in words if w.get("keyword")]
    n_words = sorted(set(n for n in n_words if len(n) >= 2), key=len, reverse=True)
    if not n_words:
        return None
    pattern = re.compile("|".join(map(re.escape, n_words)))
    m = pattern.search(nq)
    if not m:
        return None
    hit = m.group(0)
    for w in words:
        if _normalize(w.get("keyword", "")) == hit:
            return {"keyword": w.get("keyword"), "level": w.get("level", "secret")}
    return {"keyword": hit, "level": "secret"}


# ---------------------------------------------------------------------------
# ② 长度校验
# ---------------------------------------------------------------------------
def length_check(question: str) -> dict:
    """返回 {verdict: pass|ask_more, reply, detail}"""
    q = question.strip()
    if not q:
        return {"verdict": "ask_more", "reply": "请描述您的问题，我才能帮到您。", "detail": "empty"}
    if len(q) < 2 and q not in _CONTINUE_WORDS:
        return {"verdict": "ask_more", "reply": "您想问什么？说详细一点，我才能帮到您。", "detail": "too_short"}
    if len(q) > settings.PREFILTER_MAX_LEN:
        return {
            "verdict": "ask_more",
            "reply": f"问题太长了（超过 {settings.PREFILTER_MAX_LEN} 字），请精简后重试。",
            "detail": "too_long",
        }
    return {"verdict": "pass", "reply": None, "detail": "ok"}


# ---------------------------------------------------------------------------
# ③ 垃圾流量过滤
# ---------------------------------------------------------------------------
def _rate_incr(key: str, window: int, limit: int) -> bool:
    """频率计数（Redis 优先，进程内兜底）。返回 True=超限"""
    if REDIS_OK:
        try:
            k = f"prefilter:rate:{key}"
            n = _redis.incr(k)
            if n == 1:
                _redis.expire(k, window)
            return n > limit
        except Exception:
            pass
    with _mem_rate_lock:
        now = time.time()
        rec = _mem_rate.get(key)
        if not rec or now - rec[0] > window:
            _mem_rate[key] = [now, 1]
            return False
        rec[1] += 1
        return rec[1] > limit


def spam_check(question: str, user_id: str, history: list[dict]) -> dict:
    """返回 {verdict: pass|reject, reply, detail}"""
    # 频率（用户维度）
    if _rate_incr(f"user:{user_id}", settings.PREFILTER_RATE_WINDOW, settings.PREFILTER_RATE_LIMIT):
        return {"verdict": "reject", "reply": "提问过于频繁，请稍后再试。", "detail": "rate_limit"}
    # 内容重复：与最近一条用户消息相同
    for h in reversed(history or []):
        if h.get("role") == "user" and h.get("content", "").strip() == question.strip():
            return {"verdict": "reject", "reply": "您刚刚问过同样的问题，换个问法试试？", "detail": "duplicate"}
    # URL 堆砌
    urls = _URL_RE.findall(question)
    if len(urls) >= 3:
        return {"verdict": "reject", "reply": "检测到大量链接，请一次只发一个链接或问题。", "detail": "url_spam"}
    # Prompt 注入特征 → 已拆分为独立安全层（guardrails.injection_check，见 prefilter() 主流程）
    return {"verdict": "pass", "reply": None, "detail": "ok"}


# ---------------------------------------------------------------------------
# ④ 规则匹配（intent_rules：精确 → 正则 → 关键词；Redis 缓存热更新）
# ---------------------------------------------------------------------------
_CACHE_TTL = 3600
_mem_rules: Optional[list[dict]] = None
_mem_rules_lock = threading.Lock()


def _load_rules(refresh: bool = False) -> list[dict]:
    """读取启用中的规则（按 priority 降序）；Redis 缓存优先，变更后 invalidate 热更新"""
    global _mem_rules
    cache_key = "prefilter:intent_rules"
    if not refresh:
        if REDIS_OK:
            try:
                cached = _redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        with _mem_rules_lock:
            if _mem_rules is not None:
                return _mem_rules

    from sqlalchemy import text
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT trigger_type, trigger, reply_template, intent, priority "
                "FROM intent_rules WHERE enabled=TRUE ORDER BY priority DESC"
            )
        ).fetchall()
        result = [
            {"trigger_type": r[0], "trigger": r[1], "reply_template": r[2], "intent": r[3], "priority": r[4]}
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"读取 intent_rules 失败（首次启动无表？）: {e}")
        result = []
    finally:
        db.close()
    if REDIS_OK:
        try:
            _redis.setex(cache_key, _CACHE_TTL, json.dumps(result, ensure_ascii=False))
        except Exception:
            pass
    with _mem_rules_lock:
        _mem_rules = result
    return result


def invalidate_rules():
    """规则变更后调用：删 Redis 缓存 + 清进程内缓存，下个请求回源"""
    if REDIS_OK:
        try:
            _redis.delete("prefilter:intent_rules")
        except Exception:
            pass
    with _mem_rules_lock:
        global _mem_rules
        _mem_rules = None


def _match_rule(question: str) -> Optional[dict]:
    """精确 → 正则 → 关键词（各层取 priority 最高者），命中返回规则"""
    rules = _load_rules()
    if not rules:
        return None
    nq = _normalize(question)
    exact = [r for r in rules if r["trigger_type"] == "exact"]
    regex = [r for r in rules if r["trigger_type"] == "regex"]
    kw = [r for r in rules if r["trigger_type"] == "keyword"]
    # 精确（归一化后整句相等）
    for r in exact:
        if nq == _normalize(r["trigger"]):
            return r
    # 正则
    for r in regex:
        try:
            if re.search(r["trigger"], question, re.IGNORECASE):
                return r
        except re.error as e:
            logger.warning(f"非法正则规则 {r['trigger']}: {e}")
    # 关键词（触发词 >=2 字，子串命中）
    for r in kw:
        t = r["trigger"].strip()
        if len(t) >= 2 and t.lower() in nq:
            return r
    return None


# ---------------------------------------------------------------------------
# L2 轻量模型（条件触发：短文本 + 无业务特征；默认关闭；失败降级放行）
# ---------------------------------------------------------------------------
_LIGHT_PROMPT = (
    "你是一个对话意图分类器。判断用户输入是否属于「闲聊」（问候、寒暄、情绪表达、"
    "无实质信息需求的随便聊聊）。只输出一个词：chitchat 或 other。"
)


def _light_classify(question: str) -> Optional[str]:
    """返回 'chitchat' / 'other' / None（失败降级）。仅短文本触发。
    开关走系统配置（管理界面热更新），未配置时回退 settings 默认（默认关）"""
    from app.services.system_configs import get_config_bool

    if not get_config_bool("prefilter_light_enabled", settings.PREFILTER_LIGHT_ENABLED):
        return None
    if len(question.strip()) > 30:
        return None
    if _URL_RE.search(question):
        return None
    try:
        from langchain_openai import ChatOpenAI

        client = ChatOpenAI(
            model=settings.PREFILTER_LIGHT_MODEL,
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=8,
            temperature=0,
        )
        resp = client.invoke(
            [
                {"role": "system", "content": _LIGHT_PROMPT},
                {"role": "user", "content": question},
            ]
        )
        label = (resp.content or "").strip().lower()
        return label if label in ("chitchat", "other") else None
    except Exception as e:
        logger.warning(f"轻量模型分类失败，降级放行: {e}")
        return None


_LIGHT_REPLY = "嗯嗯，我在的～ 想聊点什么，或者有什么问题需要我帮忙？"


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------
def _audit(user_id: str, action: str, detail: dict):
    try:
        from app.database import SessionLocal
        from app.models.document import AuditLog

        db = SessionLocal()
        try:
            db.add(AuditLog(user_id=user_id, action=action, detail=detail))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"写前置过滤审计日志失败: {e}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
@dataclass
class PrefilterResult:
    verdict: str  # pass | direct_reply | reject | ask_more
    reply: Optional[str] = None
    layer: str = "none"  # sensitive | length | spam | rule | light_model | none
    confidence: float = 0.0
    detail: str = ""
    latency_ms: int = 0
    matched: dict = field(default_factory=dict)


def prefilter(question: str, user_id: str, history: list[dict]) -> PrefilterResult:
    """四道关卡 + L2 轻量模型，全部通过才放行进入 LangGraph（L3 LLM 兜底）"""
    t0 = time.time()

    # ① 敏感词
    hit = sensitive_check(question)
    if hit:
        r = PrefilterResult(
            "reject", "您的问题包含敏感内容，无法处理。", "sensitive", 1.0, f"hit:{hit['keyword']}({hit['level']})"
        )
        r.latency_ms = int((time.time() - t0) * 1000)
        r.matched = hit
        _audit(
            user_id,
            "prefilter_reject",
            {"layer": "sensitive", "detail": r.detail, "question": question, "latency_ms": r.latency_ms},
        )
        return r

    # ② 长度
    lc = length_check(question)
    if lc["verdict"] != "pass":
        r = PrefilterResult("ask_more", lc["reply"], "length", 1.0, lc["detail"])
        r.latency_ms = int((time.time() - t0) * 1000)
        _audit(
            user_id,
            "prefilter_ask",
            {"layer": "length", "detail": lc["detail"], "question": question, "latency_ms": r.latency_ms},
        )
        return r

    # ③ 垃圾流量
    sc = spam_check(question, user_id, history)
    if sc["verdict"] != "pass":
        r = PrefilterResult("reject", sc["reply"], "spam", 1.0, sc["detail"])
        r.latency_ms = int((time.time() - t0) * 1000)
        _audit(
            user_id,
            "prefilter_reject",
            {"layer": "spam", "detail": sc["detail"], "question": question, "latency_ms": r.latency_ms},
        )
        return r

    # ③.5 Prompt 注入防护（独立安全层，可配置开关）
    from app.agent.guardrails import injection_check

    inj = injection_check(question, user_id)
    if inj["verdict"] == "reject":
        r = PrefilterResult(
            "reject",
            "抱歉，该问题无法处理（检测到异常指令）。",
            "injection",
            1.0,
            inj["detail"],
        )
        r.latency_ms = int((time.time() - t0) * 1000)
        r.matched = {"matched": inj.get("matched", "")}
        _audit(
            user_id,
            "prefilter_reject",
            {
                "layer": "injection",
                "detail": r.detail,
                "matched": inj.get("matched", ""),
                "question": question,
                "latency_ms": r.latency_ms,
            },
        )
        return r

    # ④ 规则匹配（L1）
    rule = _match_rule(question)
    if rule:
        reply = rule["reply_template"]
        r = PrefilterResult("direct_reply", reply, "rule", 0.99, f"trigger:{rule['trigger_type']}:{rule['trigger']}")
        r.latency_ms = int((time.time() - t0) * 1000)
        r.matched = rule
        _audit(
            user_id,
            "prefilter_direct",
            {"layer": "rule", "detail": r.detail, "question": question, "latency_ms": r.latency_ms},
        )
        return r

    # L2 轻量模型（可选，条件触发）
    label = _light_classify(question)
    if label == "chitchat":
        r = PrefilterResult("direct_reply", _LIGHT_REPLY, "light_model", 0.7, "chitchat")
        r.latency_ms = int((time.time() - t0) * 1000)
        _audit(
            user_id,
            "prefilter_direct",
            {"layer": "light_model", "detail": "chitchat", "question": question, "latency_ms": r.latency_ms},
        )
        return r

    # 全部通过 → 放行（L3 LLM 兜底在图内）
    r = PrefilterResult("pass", None, "none", 0.0, "all_passed")
    r.latency_ms = int((time.time() - t0) * 1000)
    return r
