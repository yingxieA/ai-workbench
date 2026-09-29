"""敏感片段自动打标管线：标记语法 → 关键词粗筛 → LLM 兜底分类

优先级（高 → 低）：
1. 标记语法命中（文档内显式声明，如 <!-- sensitive:secret -->）→ 用标记等级
2. 关键词粗筛命中（rule_keywords 配置，Redis 缓存热更新）→ 用关键词等级
3. LLM 兜底分类（qwen 降级链）→ {sensitivity, min_level, reason}
fail-safe：LLM 分类结果解析失败 / 超时 → 默认最高安全等级 secret
"""

import json
import re
from typing import Optional

from app.services.rule_keywords import get_keywords
from app.utils.logger import get_logger

logger = get_logger("sensitive_classifier")

# 等级 → 最小可见等级
LEVEL_MIN_LEVEL = {"public": 10, "internal": 50, "secret": 100}

# 标记语法：<!-- sensitive:secret --> / <!-- sensitivity:internal -->
_MARKER_RE = re.compile(r"<!--\s*sensitiv(?:e|ity)\s*:\s*(public|internal|secret)\s*-->", re.IGNORECASE)

# 默认敏感词兜底（rule_keywords 为空时也能跑；运营期在后台维护，会覆盖这里）
DEFAULT_KEYWORDS = [
    "薪酬",
    "工资",
    "奖金",
    "薪资",
    "绩效工资",
    "社保基数",
    "身份证",
    "身份证号",
    "银行卡",
    "银行卡号",
    "手机号",
    "住址",
    "家庭住址",
    "商业机密",
    "战略规划",
    "未公开",
    "内部代码",
    "客户隐私",
    "客户名单",
]


def extract_marker(text: str) -> Optional[dict]:
    """① 标记语法：文档内显式声明片段等级"""
    m = _MARKER_RE.search(text)
    if m:
        level = m.group(1).lower()
        return {
            "sensitivity": level,
            "min_level": LEVEL_MIN_LEVEL[level],
            "reason": f"标记语法声明（{level}）",
        }
    return None


def match_keyword(text: str) -> Optional[dict]:
    """② 关键词粗筛：命中即返回等级（不走 LLM）"""
    rules = get_keywords("sensitive") or [{"keyword": k, "level": "secret"} for k in DEFAULT_KEYWORDS]
    for rule in rules:
        kw = rule.get("keyword")
        if kw and kw in text:
            level = rule.get("level") or "secret"
            return {
                "sensitivity": level,
                "min_level": LEVEL_MIN_LEVEL.get(level, 100),
                "reason": f"关键词命中：{kw}",
            }
    return None


_CLASSIFY_PROMPT = """你是企业知识库的安全分类器。对下列每个文本片段判断敏感等级。

等级定义：
- public：可对所有员工公开（产品介绍、FAQ、培训资料）
- internal：仅内部员工可见（技术方案、内部制度）
- secret：仅限特定角色/高管（薪酬、财务、人事、客户隐私、商业机密）

输出严格 JSON 数组，每项格式：
{{"index": 片段序号, "sensitivity": "public|internal|secret", "reason": "一句话依据"}}

片段列表：
{chunks}
"""


def _extract_json(resp_text: str) -> Optional[list]:
    """健壮解析：AIMessage.content 可能是 str / list(blocks)；剥离 markdown 代码块后取 JSON 数组"""
    if isinstance(resp_text, list):  # 多模态 blocks（text/image）
        parts = []
        for b in resp_text:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        resp_text = "".join(parts)
    if not isinstance(resp_text, str) or not resp_text.strip():
        raise ValueError("空响应")
    text = resp_text.strip()
    # 剥离 ```json ... ``` 代码块
    import re as _re

    m = _re.search(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 直接解析
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            return data["items"]
    except Exception:
        pass
    # 兜底：截取第一个 [ 到最后一个 ]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"无法解析为 JSON: {text[:200]}")


def llm_classify(texts: list[str]) -> list[dict]:
    """③ LLM 兜底批量分类：调用降级链 chat_complete，JSON 模式"""
    from app.agent.llm import chat_complete

    payload = "\n".join(f"[{i}] {t[:800]}" for i, t in enumerate(texts))
    results: list[dict] = []
    try:
        resp_text, _ = chat_complete(
            [
                {"role": "user", "content": _CLASSIFY_PROMPT.format(chunks=payload)},
            ]
        )
        data = _extract_json(resp_text)
        by_index = {}
        for item in data:
            if isinstance(item, dict) and "index" in item:
                by_index[int(item["index"])] = item
        for i in range(len(texts)):
            item = by_index.get(i, {})
            level = (item.get("sensitivity") or "secret").lower()
            if level not in LEVEL_MIN_LEVEL:
                level = "secret"
            results.append(
                {
                    "index": i,
                    "sensitivity": level,
                    "min_level": LEVEL_MIN_LEVEL[level],
                    "reason": item.get("reason", "") or f"LLM 分类：{level}",
                }
            )
    except Exception as e:
        logger.error(f"LLM 分类失败，fail-safe 默认 secret: {e}")
        results = [
            {"index": i, "sensitivity": "secret", "min_level": 100, "reason": "LLM 分类失败，fail-safe 默认最高等级"}
            for i in range(len(texts))
        ]
    return results


def label_chunk(text: str) -> dict:
    """单块打标（优先级：标记语法 → 关键词 → LLM）"""
    marker = extract_marker(text)
    if marker:
        return marker
    kw = match_keyword(text)
    if kw:
        return kw
    # LLM 兜底（单块）
    res = llm_classify([text])
    return {k: v for k, v in res[0].items() if k != "index"}


def label_chunks_batch(texts: list[str]) -> list[dict]:
    """批量打标：先标记语法 + 关键词，未判定的交给 LLM 兜底（攒批一次调用）"""
    results = []
    llm_candidates: list[int] = []
    for i, t in enumerate(texts):
        marker = extract_marker(t)
        if marker:
            results.append(marker)
            continue
        kw = match_keyword(t)
        if kw:
            results.append(kw)
            continue
        results.append(None)  # 占位
        llm_candidates.append(i)

    if llm_candidates:
        llm_texts = [texts[i] for i in llm_candidates]
        llm_results = llm_classify(llm_texts)
        for i, item in zip(llm_candidates, llm_results):
            results[i] = {k: v for k, v in item.items() if k != "index"}

    # 兜底：仍为 None 的（异常情况）默认 secret
    for i, r in enumerate(results):
        if r is None:
            results[i] = {
                "sensitivity": "secret",
                "min_level": 100,
                "reason": "打标异常，fail-safe 默认最高等级",
            }
    return results
