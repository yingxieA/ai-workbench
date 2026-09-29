# -*- coding: utf-8 -*-
"""系统配置（KV）服务：PostgreSQL 持久化 + Redis 缓存热更新

生效链路：管理界面改配置 → 写 DB → 删 Redis 缓存 → 下个请求回源生效（无需重启）
"""

import json
import threading

from sqlalchemy import text

from app.database import SessionLocal
from app.utils.logger import get_logger

logger = get_logger("system_configs")

try:
    import redis as redis_lib
    from app.config import settings

    _redis = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis.ping()
    REDIS_OK = True
except Exception as e:
    logger.warning(f"Redis 不可用，系统配置缓存降级为进程内缓存: {e}")
    _redis = None
    REDIS_OK = False

_mem_cache: dict[str, str] = {}
_mem_lock = threading.Lock()

_CACHE_TTL = 3600
_CACHE_KEY = "system:configs"


def _read_all_from_db() -> dict[str, str]:
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT key, value FROM system_configs")).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception as e:
        logger.warning(f"读取 system_configs 失败: {e}")
        return {}
    finally:
        db.close()


def get_configs(refresh: bool = False) -> dict[str, str]:
    """读取全部配置（Redis 缓存优先）"""
    if not refresh:
        if REDIS_OK:
            try:
                cached = _redis.get(_CACHE_KEY)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        with _mem_lock:
            if _mem_cache:
                return dict(_mem_cache)

    result = _read_all_from_db()
    if REDIS_OK:
        try:
            _redis.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(result, ensure_ascii=False))
        except Exception:
            pass
    with _mem_lock:
        _mem_cache.clear()
        _mem_cache.update(result)
    return result


def get_config(key: str, default: str = "") -> str:
    return get_configs().get(key, default)


def get_config_bool(key: str, default: bool = False) -> bool:
    val = get_configs().get(key)
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def set_config(key: str, value: str, description: str = "", updated_by: str = "") -> dict:
    """写入配置（不存在则插入），并热更新缓存"""
    db = SessionLocal()
    try:
        exists = db.execute(text("SELECT key FROM system_configs WHERE key=:k"), {"k": key}).fetchone()
        if exists:
            db.execute(
                text(
                    "UPDATE system_configs SET value=:v, description=:d, updated_by=:u, updated_at=now() WHERE key=:k"
                ),
                {"v": value, "d": description, "u": updated_by, "k": key},
            )
            action = "update"
        else:
            db.execute(
                text("INSERT INTO system_configs (key, value, description, updated_by) VALUES (:k,:v,:d,:u)"),
                {"k": key, "v": value, "d": description, "u": updated_by},
            )
            action = "add"
        db.commit()
    finally:
        db.close()

    # 热更新：删 Redis 缓存 + 清空进程内整体缓存（get_configs 优先读整体缓存，只 pop 单 key 会读到旧值）
    if REDIS_OK:
        try:
            _redis.delete(_CACHE_KEY)
        except Exception:
            pass
    with _mem_lock:
        _mem_cache.clear()
    return {"ok": True, "action": action, "key": key, "value": value}


def list_configs() -> list[dict]:
    """列出全部配置（含描述/操作人/更新时间），供管理界面使用"""
    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT key, value, description, updated_by, updated_at FROM system_configs ORDER BY key")
        ).fetchall()
        return [
            {"key": r[0], "value": r[1], "description": r[2], "updated_by": r[3], "updated_at": str(r[4])} for r in rows
        ]
    finally:
        db.close()
