# -*- coding: utf-8 -*-
"""幻觉检测（LLM as a Judge，P2.6）：RAG 回答落库后异步判定"回答是否被检索片段支撑"
- 用 qwen-turbo（便宜，判断支撑性任务足够）比对 question + contexts + answer
- 输出 verdict: supported / partially / unsupported + score(0-1) + reason
- 独立线程执行（不阻塞 SSE 主链路），结果更新 chat_router_logs
"""

import json
import re
import threading
from openai import OpenAI

from app.config import settings
from app.database import SessionLocal
from app.utils.logger import get_logger

logger = get_logger("hallucination_judge")

JUDGE_MODEL = "qwen-turbo"

JUDGE_PROMPT = """你是严谨的幻觉检测器。你的任务：判断【回答】中的关键事实是否被【检索片段】支撑。

【检索片段】
{contexts}

【回答】
{answer}

判断规则：
1. 逐条检查回答中的关键事实（数字、日期、名称、结论、引用内容），是否能在片段中找到依据。
2. 全部关键事实都能在片段中找到对应依据 → supported
3. 部分关键事实找不到依据（或片段为空、完全无关）→ partially
4. 回答与片段无关 / 片段无法支撑任何关键事实（可能编造）→ unsupported
5. 只判"支撑性"，不评判回答质量、风格、对错。

只输出一个 JSON，不要任何其他文字：
{{"verdict": "supported | partially | unsupported", "score": 0.0到1.0之间的数字（1=完全被支撑，0=完全无支撑）, "reason": "一句话说明依据"}}"""


def _call_judge(question: str, contexts: list[str], answer: str) -> dict:
    """调用 qwen-turbo 判定，返回 {verdict, score, reason}；异常时返回默认值"""
    if not answer or not answer.strip():
        return {"verdict": "unsupported", "score": 0.0, "reason": "回答为空"}
    context_text = "\n\n".join([f"[[{i + 1}]] {c}" for i, c in enumerate(contexts)]) if contexts else "（无检索片段）"
    prompt = JUDGE_PROMPT.format(contexts=context_text, answer=answer[:3000])
    try:
        client = OpenAI(
            api_key=settings.DASHSCOPE_API_KEY, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": "你是幻觉检测器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        text = resp.choices[0].message.content or ""
        return _parse(text)
    except Exception as e:
        logger.warning(f"Judge 调用失败: {e}")
        return {"verdict": "unsupported", "score": 0.0, "reason": f"Judge 调用失败: {str(e)[:100]}"}


def _parse(text: str) -> dict:
    """解析 Judge 输出（宽容：JSON 块 / 裸 JSON / 正则提取）"""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            verdict = str(obj.get("verdict", "")).strip().lower()
            if verdict not in ("supported", "partially", "unsupported"):
                # 兼容部分 / 完全等表述
                if "partially" in verdict or "部分" in verdict:
                    verdict = "partially"
                elif "support" in verdict or "支撑" in verdict or "支持" in verdict:
                    verdict = "supported"
                else:
                    verdict = "unsupported"
            try:
                score = float(obj.get("score", 0))
            except Exception:
                score = 0.0
            return {"verdict": verdict, "score": max(0.0, min(1.0, score)), "reason": str(obj.get("reason", ""))[:300]}
        except Exception:
            pass
    return {"verdict": "unsupported", "score": 0.0, "reason": "Judge 输出无法解析"}


def judge_and_persist(session_id: str, question: str, contexts: list[str], answer: str):
    """后台线程入口：判定 + 更新最近一条 chat_router_logs"""
    try:
        result = _call_judge(question, contexts, answer)
        db = SessionLocal()
        try:
            from app.models.document import ChatRouterLog

            log = (
                db.query(ChatRouterLog)
                .filter(ChatRouterLog.session_id == session_id, ChatRouterLog.question == question[:200])
                .order_by(ChatRouterLog.created_at.desc())
                .first()
            )
            if log:
                log.hallucination_verdict = result["verdict"]
                log.hallucination_score = result["score"]
                log.judge_reason = result["reason"]
                db.commit()
                logger.info(f"幻觉检测完成: session={session_id} verdict={result['verdict']} score={result['score']}")
            else:
                logger.warning(f"幻觉检测: 未找到匹配的 router log, session={session_id}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"幻觉检测任务异常: {e}")


def run_judge_async(session_id: str, question: str, contexts: list[str], answer: str):
    """异步触发（独立线程，不阻塞主链路）"""
    threading.Thread(target=judge_and_persist, args=(session_id, question, contexts, answer), daemon=True).start()
