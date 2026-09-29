# -*- coding: utf-8 -*-
"""记忆与多轮对话服务（P2.5）

三层记忆：
- 短期记忆：滑动窗口（chat_window_rounds 轮）内原始消息直传；窗口外消息走 LLM 增量摘要压缩，
  摘要存 session_summaries（version 累积，可回溯），prompt 组装为「摘要 + 近 N 轮」
- 长期记忆：对话后 LLM 抽取事实型用户画像（偏好/预算/家庭等），增量合并存 user_profiles（JSONB）
- 会话恢复：Redis 缓存最近窗口消息（TTL，快读）+ PG 持久化（权威，miss 回源回填）

全部开关走 system_configs（管理界面可热配）：chat_window_rounds / chat_summary_enabled / chat_redis_cache_enabled
"""

from __future__ import annotations

import json

from sqlalchemy import text

from app.database import SessionLocal
from app.utils.logger import get_logger

logger = get_logger("memory_service")

# ---------- 配置读取 ----------


def get_window_rounds() -> int:
    from app.services.system_configs import get_config

    try:
        return max(2, int(get_config("chat_window_rounds", "10")))
    except (TypeError, ValueError):
        return 10


def summary_enabled() -> bool:
    from app.services.system_configs import get_config_bool

    return get_config_bool("chat_summary_enabled", True)


def redis_cache_enabled() -> bool:
    from app.services.system_configs import get_config_bool

    return get_config_bool("chat_redis_cache_enabled", True)


# ---------- Redis 热缓存（会话恢复快读） ----------

_redis = None
_REDIS_OK = False


def _get_redis():
    global _redis, _REDIS_OK
    if _redis is not None:
        return _redis
    try:
        import redis as redis_lib
        from app.config import settings

        _redis = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        _redis.ping()
        _REDIS_OK = True
    except Exception as e:
        logger.warning(f"Redis 不可用，会话缓存降级: {e}")
        _redis = False
    return _redis if _REDIS_OK else None


_CACHE_TTL = 1800  # 30 分钟活跃会话


def _cache_key(session_id: str) -> str:
    return f"chat:history:{session_id}"


def cache_put_history(session_id: str, messages: list[dict]) -> None:
    """写历史热缓存（裁剪到窗口轮数，只缓存最近窗口）"""
    if not redis_cache_enabled():
        return
    r = _get_redis()
    if not r:
        return
    try:
        r.setex(
            _cache_key(session_id), _CACHE_TTL, json.dumps(messages[-get_window_rounds() * 2 :], ensure_ascii=False)
        )
    except Exception as e:
        logger.warning(f"写入会话缓存失败: {e}")


def cache_get_history(session_id: str) -> list[dict] | None:
    """读历史热缓存；miss 返回 None（调用方回源 PG）"""
    if not redis_cache_enabled():
        return None
    r = _get_redis()
    if not r:
        return None
    try:
        raw = r.get(_cache_key(session_id))
        if not raw:
            return None
        msgs = json.loads(raw)
        return msgs if isinstance(msgs, list) else None
    except Exception as e:
        logger.warning(f"读取会话缓存失败: {e}")
        return None


def cache_invalidate(session_id: str) -> None:
    r = _get_redis()
    if not r:
        return
    try:
        r.delete(_cache_key(session_id))
    except Exception:
        pass


# ---------- PG 持久化历史（权威） ----------


def load_history_from_pg(session_id: str, limit: int = 50) -> list[dict]:
    """从 PG 读最近 limit 条消息（含当前请求已入库的最新一条）"""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT role, content FROM chat_messages WHERE session_id=:sid AND is_deleted=FALSE ORDER BY created_at DESC LIMIT :lim"
            ),
            {"sid": session_id, "lim": limit},
        ).fetchall()
        msgs = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
        return msgs
    except Exception as e:
        logger.warning(f"读取 PG 历史失败: {e}")
        return []
    finally:
        db.close()


def get_history_messages(session_id: str) -> list[dict]:
    """会话恢复读取：Redis 优先，miss 回源 PG 并回填"""
    cached = cache_get_history(session_id)
    if cached is not None:
        return cached
    msgs = load_history_from_pg(session_id)
    if msgs:
        cache_put_history(session_id, msgs)
    return msgs


# ---------- 短期记忆：摘要压缩 ----------

_SUMMARY_PROMPT = """你是会话摘要器。把一段对话压缩成简洁的要点摘要（保留事实、决定、用户偏好、待办事项，去掉寒暄）。
输入格式为 JSON 数组：[{"role": "user", "content": "..."}, ...]
要求：
1. 只输出摘要正文，不要 JSON、不要解释
2. 控制在 200 字以内
3. 若提供"已有摘要"，应把新信息合并进去（保留旧要点，补充新要点），不要重复已有内容"""


def summarize_messages(messages: list[dict], prev_summary: str = "") -> str:
    """LLM 增量摘要：旧摘要 + 新溢出消息 → 新摘要（qwen-turbo，失败降级截断）"""
    if not messages:
        return prev_summary
    try:
        from langchain_openai import ChatOpenAI
        from app.config import settings

        client = ChatOpenAI(
            model=getattr(settings, "PREFILTER_LIGHT_MODEL", "qwen-turbo"),
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=15,
            temperature=0.2,
        )
        user_content = json.dumps(messages, ensure_ascii=False)[:6000]
        if prev_summary:
            user_content = f"已有摘要：{prev_summary}\n\n新增对话：{user_content}"
        resp = client.invoke(
            [
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        summary = (resp.content or "").strip()
        return summary[:500] if summary else prev_summary
    except Exception as e:
        logger.warning(f"摘要生成失败，降级保留旧摘要: {str(e)[:120]}")
        return prev_summary


def get_session_summary(session_id: str) -> str:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT summary FROM session_summaries WHERE session_id=:sid"), {"sid": session_id}
        ).fetchone()
        return row[0] if row else ""
    finally:
        db.close()


def update_session_summary(session_id: str, new_summary: str) -> None:
    """累积更新：新摘要覆盖写（version+1），摘要本身已含旧要点（增量合并），不丢历史"""
    if not new_summary:
        return
    db = SessionLocal()
    try:
        exists = db.execute(
            text("SELECT 1 FROM session_summaries WHERE session_id=:sid"), {"sid": session_id}
        ).fetchone()
        if exists:
            db.execute(
                text(
                    "UPDATE session_summaries SET summary=:s, version=version+1, updated_at=now() WHERE session_id=:sid"
                ),
                {"s": new_summary, "sid": session_id},
            )
        else:
            db.execute(
                text("INSERT INTO session_summaries (session_id, summary, version) VALUES (:sid,:s,1)"),
                {"s": new_summary, "sid": session_id},
            )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"更新摘要失败: {e}")
    finally:
        db.close()


def build_chat_context(session_id: str, current_question: str) -> dict:
    """组装图输入：滑动窗口 + 摘要
    返回 {history(窗口内), summary(窗口外摘要), window_rounds}
    - 全部消息 = Redis/PG 最近消息（含刚入库的当前提问，调用方传 history 或回源）
    - 窗口内：最近 window_rounds*2 条（rounds 轮 = 用户+助手 2 条/轮）
    - 窗口外：生成/读取摘要（异步更新由 chat.py 在回答完成后触发）
    """
    window = get_window_rounds()
    all_msgs = get_history_messages(session_id)  # 含当前提问（最后一条）
    if len(all_msgs) <= window * 2:
        return {"history": all_msgs[:-1], "summary": get_session_summary(session_id), "window_rounds": window}

    # 窗口溢出：窗口内直传，窗口外 → 摘要（增量近似：只重摘要最近 20 条溢出消息 + 旧摘要合并，
    # 更老的信息已浓缩在旧摘要里，避免全量重算随对话无限增长）
    in_window = all_msgs[-(window * 2) :]
    out_window = all_msgs[: -(window * 2)][-20:]
    summary = get_session_summary(session_id)
    # 摘要开关关闭时：窗口外消息直接丢弃（只传最近窗口，防 token 超限）
    if summary_enabled() and out_window:
        prev = summary
        summary = summarize_messages(out_window, prev)
        if summary and summary != prev:
            update_session_summary(session_id, summary)
    return {"history": in_window[:-1], "summary": summary, "window_rounds": window}


# ---------- 长期记忆：用户画像 ----------

_PROFILE_PROMPT = """你是用户画像抽取器。从对话中提取关于用户本人的稳定事实（偏好、习惯、预算、家庭、职业、目标等）。
输入为 JSON 数组对话（最后一条是当前提问）。要求：
1. 只输出 JSON，格式：{"属性名": "值", ...}，如 {"旅游偏好": "带父母", "预算": "5000元"}
2. 只抽明确说出的稳定事实，不推断、不臆测
3. 若属性已存在（提供现有画像），输出合并后的完整画像
4. 属性名用 2-8 个字"""


def extract_profile(user_id: str, messages: list[dict]) -> None:
    """对话后异步抽取/更新用户画像：增量合并（同 key 新值覆盖 + 时间戳）"""
    if not user_id or not messages:
        return
    try:
        from langchain_openai import ChatOpenAI
        from app.config import settings

        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT profile_json, version FROM user_profiles WHERE user_id=:uid"), {"uid": user_id}
            ).fetchone()
            prev_json = row[0] if row else {}
            prev_version = row[1] if row else 0
        finally:
            db.close()
        prev_json = prev_json or {}

        client = ChatOpenAI(
            model=getattr(settings, "PREFILTER_LIGHT_MODEL", "qwen-turbo"),
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=15,
            temperature=0,
        )
        user_content = json.dumps(messages[-6:], ensure_ascii=False)[:5000]
        if prev_json:
            user_content = f"现有画像：{json.dumps(prev_json, ensure_ascii=False)}\n\n新增对话：{user_content}"
        resp = client.invoke(
            [
                {"role": "system", "content": _PROFILE_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        text_out = (resp.content or "").strip()
        # 容错解析 JSON
        import re

        m = re.search(r"\{.*\}", text_out, re.DOTALL)
        if not m:
            return
        new_json = json.loads(m.group(0))
        if not isinstance(new_json, dict):
            return
        merged = {**prev_json, **new_json}  # 增量合并：同 key 新值覆盖
        if merged == prev_json:
            return
        db = SessionLocal()
        try:
            exists = db.execute(text("SELECT 1 FROM user_profiles WHERE user_id=:uid"), {"uid": user_id}).fetchone()
            if exists:
                db.execute(
                    text(
                        "UPDATE user_profiles SET profile_json=:pj, version=version+1, updated_at=now() WHERE user_id=:uid"
                    ),
                    {"pj": json.dumps(merged, ensure_ascii=False), "uid": user_id},
                )
            else:
                db.execute(
                    text("INSERT INTO user_profiles (user_id, profile_json, version) VALUES (:uid,:pj,1)"),
                    {"uid": user_id, "pj": json.dumps(merged, ensure_ascii=False)},
                )
            db.commit()
            logger.info(f"用户画像更新: user={user_id[:8]} 新增属性={list(new_json.keys())} v{prev_version + 1}")
        except Exception as e:
            db.rollback()
            logger.warning(f"画像落库失败: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"画像抽取失败: {str(e)[:120]}")


def get_user_profile(user_id: str) -> dict:
    """读用户画像（供 prompt 注入）"""
    if not user_id:
        return {}
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT profile_json FROM user_profiles WHERE user_id=:uid"), {"uid": user_id}).fetchone()
        return row[0] if row else {}
    except Exception:
        return {}
    finally:
        db.close()


def profile_to_prompt(profile: dict) -> str:
    """画像 → system prompt 片段（空画像返回空串）"""
    if not profile:
        return ""
    items = [f"{k}：{v}" for k, v in profile.items() if v]
    if not items:
        return ""
    return "【用户画像】" + "；".join(items)
