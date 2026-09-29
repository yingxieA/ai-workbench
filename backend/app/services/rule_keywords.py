"""敏感词 / 分类关键词运营配置：PostgreSQL 持久化 + Redis 缓存热更新

生效链路：admin 后台加词 → 写 DB → 删 Redis 缓存 → 下个请求自动回源读新词（无需重启）
"""

import json
import threading

from sqlalchemy import text

from app.database import SessionLocal
from app.utils.logger import get_logger

logger = get_logger("rule_keywords")

try:
    import redis as redis_lib
    from app.config import settings

    _redis = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis.ping()
    REDIS_OK = True
except Exception as e:
    logger.warning(f"Redis 不可用，关键词缓存降级为进程内缓存: {e}")
    _redis = None
    REDIS_OK = False

# 进程内缓存兜底（Redis 挂了也能热更新）
_mem_cache: dict[str, list[dict]] = {}
_mem_lock = threading.Lock()

_CACHE_TTL = 3600  # 秒


def _cache_key(category: str) -> str:
    return f"rule:keywords:{category}"


def get_keywords(category: str, refresh: bool = False) -> list[dict]:
    """按类别读取启用中的关键词，返回 [{keyword, level}]（Redis 缓存优先）"""
    key = _cache_key(category)
    if not refresh:
        if REDIS_OK:
            try:
                cached = _redis.get(key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        with _mem_lock:
            if category in _mem_cache:
                return _mem_cache[category]

    # 回源 DB
    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT keyword, level FROM rule_keywords WHERE category=:c AND enabled=TRUE"),
            {"c": category},
        ).fetchall()
        result = [{"keyword": r[0], "level": r[1]} for r in rows]
    finally:
        db.close()

    # 写缓存
    if REDIS_OK:
        try:
            _redis.setex(key, _CACHE_TTL, json.dumps(result, ensure_ascii=False))
        except Exception:
            pass
    with _mem_lock:
        _mem_cache[category] = result
    return result


def invalidate(category: str):
    """配置变更后删除缓存，下个请求回源"""
    key = _cache_key(category)
    if REDIS_OK:
        try:
            _redis.delete(key)
        except Exception:
            pass
    with _mem_lock:
        _mem_cache.pop(category, None)


def add_keyword(category: str, keyword: str, level: str, created_by: str) -> dict:
    """新增关键词（幂等：同 category+keyword 存在则更新 level）"""
    db = SessionLocal()
    try:
        exists = db.execute(
            text("SELECT id FROM rule_keywords WHERE category=:c AND keyword=:k"),
            {"c": category, "k": keyword},
        ).fetchone()
        if exists:
            db.execute(
                text("UPDATE rule_keywords SET level=:l, enabled=TRUE, created_by=:u WHERE id=:id"),
                {"l": level, "u": created_by, "id": exists[0]},
            )
            action = "update"
        else:
            db.execute(
                text(
                    "INSERT INTO rule_keywords (category, keyword, level, enabled, created_by) VALUES (:c,:k,:l,TRUE,:u)"
                ),
                {"c": category, "k": keyword, "l": level, "u": created_by},
            )
            action = "add"
        db.commit()
    finally:
        db.close()
    invalidate(category)
    return {"ok": True, "action": action}


def delete_keyword(category: str, keyword: str) -> dict:
    """软删除关键词（enabled=FALSE）"""
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE rule_keywords SET enabled=FALSE WHERE category=:c AND keyword=:k"),
            {"c": category, "k": keyword},
        )
        db.commit()
    finally:
        db.close()
    invalidate(category)
    return {"ok": True}


def list_keywords(category: str | None = None) -> list[dict]:
    """列出全部（含禁用），供管理界面使用"""
    db = SessionLocal()
    try:
        if category:
            rows = db.execute(
                text(
                    "SELECT category, keyword, level, enabled, created_by, created_at FROM rule_keywords WHERE category=:c ORDER BY created_at DESC"
                ),
                {"c": category},
            ).fetchall()
        else:
            rows = db.execute(
                text(
                    "SELECT category, keyword, level, enabled, created_by, created_at FROM rule_keywords ORDER BY category, created_at DESC"
                ),
            ).fetchall()
        return [
            {
                "category": r[0],
                "keyword": r[1],
                "level": r[2],
                "enabled": r[3],
                "created_by": r[4],
                "created_at": str(r[5]),
            }
            for r in rows
        ]
    finally:
        db.close()
