"""内置工具：date_calculator（日期推算） / knowledge_base_query（知识库检索）
- date_calculator：N 天/周前后、两日期差（纯计算，避免 LLM 日期幻觉）
- knowledge_base_query：把私有知识库检索做成工具（供 Agent Loop 多步推理在需要时自行检索）
"""

from __future__ import annotations

import datetime as _dt
import re

from app.agent.tools.registry import ToolSpec, ToolContext, RISK_NORMAL
from app.utils.logger import get_logger

logger = get_logger("tool_builtin_kb")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ---------- date_calculator ----------


def _parse_date(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s.strip())


def _date_calculator(args: dict, ctx: ToolContext) -> dict:
    action = args.get("action") or "today"
    try:
        if action == "today":
            return {"date": _dt.date.today().isoformat(), "note": "系统当前日期（UTC+8）"}
        base = _parse_date(args.get("base_date") or _dt.date.today().isoformat())
        if action == "add_days":
            days = int(args.get("days") or 0)
            result = base + _dt.timedelta(days=days)
            return {
                "base_date": base.isoformat(),
                "offset_days": days,
                "result_date": result.isoformat(),
                "weekday": result.strftime("%A"),
            }
        if action == "add_weeks":
            weeks = int(args.get("weeks") or 0)
            result = base + _dt.timedelta(weeks=weeks)
            return {
                "base_date": base.isoformat(),
                "offset_weeks": weeks,
                "result_date": result.isoformat(),
                "weekday": result.strftime("%A"),
            }
        if action == "diff":
            date2 = _parse_date(args.get("date2") or "")
            diff = (date2 - base).days
            return {
                "date1": base.isoformat(),
                "date2": date2.isoformat(),
                "diff_days": diff,
                "note": "正数表示 date2 在 date1 之后",
            }
        if action == "weekday":
            target = _parse_date(args.get("date") or _dt.date.today().isoformat())
            return {
                "date": target.isoformat(),
                "weekday": target.strftime("%A"),
                "weekday_cn": _WEEKDAYS_CN[target.weekday()],
                "note": f"{target.isoformat()} 是 {_WEEKDAYS_CN[target.weekday()]}",
            }
        return {"error": f"未知 action: {action}（可选 today/add_days/add_weeks/diff/weekday）"}
    except ValueError as e:
        return {"error": f"日期格式错误（需 YYYY-MM-DD）: {str(e)[:100]}"}


# ---------- knowledge_base_query ----------


def _kb_query(args: dict, ctx: ToolContext) -> dict:
    query = (args.get("query") or "").strip()
    top_k = min(int(args.get("top_k") or 5), 10)
    if not query:
        return {"error": "query 不能为空"}
    try:
        # 复用现有检索链路（LlamaIndex 召回 + 重排 + 权限过滤）
        from app.agent.retriever import get_llama_retriever, set_retrieve_scope
        from app.database import SessionLocal
        from sqlalchemy import text

        uid = ctx.user_id
        lv = 10
        if uid:
            db = SessionLocal()
            try:
                row = db.execute(
                    text("SELECT role_level FROM users WHERE id = :uid AND is_deleted = FALSE"), {"uid": uid}
                ).fetchone()
                lv = row[0] if row else 10
            except Exception as e:
                logger.warning(f"知识库工具读取权限失败，默认 10: {e}")
            finally:
                db.close()
        set_retrieve_scope(uid, lv)

        retriever = get_llama_retriever()
        nodes = retriever.retrieve(query)
        docs = [(n.node.text, n.node.metadata.get("title", "未知文档")) for n in nodes[: top_k * 2]]
        from app.services.reranker import rerank

        reranked = rerank(query, [d[0] for d in docs], top_k=top_k)
        hits = []
        for doc, score in reranked:
            if score < 0.4:
                continue
            title = next((t for t, _ in [(d[1], d[0]) for d in docs] if _ == doc), "未知文档")
            hits.append({"title": title, "content": doc[:1000], "score": round(float(score), 3)})
        return {"query": query, "top_k": top_k, "hits": hits, "total_hits": len(hits)}
    except Exception as e:
        logger.error(f"知识库检索失败: {e}")
        return {"error": f"知识库检索失败: {str(e)[:150]}"}


def register_kb_tools(reg) -> None:
    reg.register(
        ToolSpec(
            name="date_calculator",
            description="日期推算：今天是几号、N 天/周前后是哪天、两个日期相差多少天、任意日期是星期几。任何涉及「几天后/上周/下月/日期差/星期几/某年某月某日是周几」的问题都应先用它，而不是靠模型心算",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["today", "add_days", "add_weeks", "diff", "weekday"],
                        "description": "today=当前日期；add_days=加 N 天；add_weeks=加 N 周；diff=两日期差；weekday=查某日期是星期几",
                    },
                    "base_date": {"type": "string", "description": "基准日期 YYYY-MM-DD（默认今天）"},
                    "days": {"type": "integer", "description": "add_days 用的天数（可负）"},
                    "weeks": {"type": "integer", "description": "add_weeks 用的周数（可负）"},
                    "date2": {"type": "string", "description": "diff 用的第二个日期 YYYY-MM-DD"},
                    "date": {"type": "string", "description": "weekday 用的目标日期 YYYY-MM-DD"},
                },
                "required": ["action"],
            },
            handler=_date_calculator,
            risk_level=RISK_NORMAL,
            tags=["time", "date", "calc"],
        )
    )
    reg.register(
        ToolSpec(
            name="knowledge_base_query",
            description="查询本项目私有知识库（按用户权限过滤）。回答业务事实/产品规则/技术教程类问题、或需要引用库内资料时使用。多步任务中若某步需要库内资料，也应调用它",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题（与用户问题的相关子问题）"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
            handler=_kb_query,
            risk_level=RISK_NORMAL,
            tags=["rag", "kb", "search"],
        )
    )
