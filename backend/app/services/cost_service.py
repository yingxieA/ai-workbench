# -*- coding: utf-8 -*-
"""成本监控服务（P2.6 阶段3）：
- 单价配置：SystemConfig.model_pricing（JSON，元/1K tokens），热更新生效
- 当日成本：Redis 计数（key=cost:daily:YYYY-MM-DD，TTL 3 天），Redis 挂则 SQL SUM 回填
- 超限降级判定：cost_guard_enabled && 当日累计 > daily_cost_limit → 跳过最高档模型
"""

import json
import threading
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.services.system_configs import get_config, get_config_bool
from app.utils.logger import get_logger

logger = get_logger("cost_service")

BJ_TZ = timezone(timedelta(hours=8))

try:
    import redis as redis_lib
    from app.config import settings

    _redis = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    _redis.ping()
    REDIS_OK = True
except Exception as e:
    logger.warning(f"成本计数 Redis 不可用，降级 SQL SUM: {e}")
    _redis = None
    REDIS_OK = False

_pricing_cache: dict | None = None
_pricing_ts = 0.0
_pricing_lock = threading.Lock()


def get_pricing() -> dict:
    """模型单价（元/1K tokens），60s 进程内缓存 + 配置热更新兜底"""
    global _pricing_cache, _pricing_ts
    with _pricing_lock:
        now = datetime.now().timestamp()
        if _pricing_cache is not None and now - _pricing_ts < 60:
            return _pricing_cache
        raw = get_config("model_pricing", "{}")
        try:
            pricing = json.loads(raw) if raw else {}
        except Exception:
            pricing = {}
        # 默认兜底单价（配置缺失时）
        _pricing_cache = {
            "qwen-max": 0.02,
            "qwen-plus": 0.005,
            "qwen-turbo": 0.0005,
            "deepseek-chat": 0.002,
            **pricing,
        }
        _pricing_ts = now
        return _pricing_cache


def price_of(model: str) -> float:
    return float(get_pricing().get(model, 0.0) or 0.0)


def calc_cost(model: str, tokens: int) -> float:
    """单次调用成本（元）"""
    return round(tokens / 1000.0 * price_of(model), 6)


def _today() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d")


def add_cost(model: str, tokens: int) -> float:
    """累计当日成本（Redis 原子累加），返回本次 cost；失败不影响主流程"""
    cost = calc_cost(model, tokens)
    if cost <= 0:
        return cost
    if REDIS_OK:
        try:
            key = f"cost:daily:{_today()}"
            pipe = _redis.pipeline()
            pipe.incrbyfloat(key, cost)
            pipe.expire(key, 3 * 86400)
            pipe.execute()
            return cost
        except Exception as e:
            logger.warning(f"Redis 成本累加失败: {e}")
    # 降级：直接落库（chat_router_logs 由 audit 节点写入，此处仅日志提示）
    logger.warning(f"成本未入 Redis，需靠审计表 SUM: model={model} cost={cost}")
    return cost


def daily_cost() -> float:
    """当日累计成本：Redis 优先，miss/异常回源 SQL SUM"""
    if REDIS_OK:
        try:
            val = _redis.get(f"cost:daily:{_today()}")
            if val is not None:
                return float(val)
        except Exception as e:
            logger.warning(f"读取当日成本失败: {e}")
    # SQL SUM 回填
    try:
        from sqlalchemy import text

        db = SessionLocal()
        try:
            day = datetime.now(BJ_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            row = db.execute(
                text("SELECT COALESCE(SUM(cost), 0) FROM chat_router_logs WHERE created_at >= :d"),
                {"d": day},
            ).fetchone()
            return float(row[0] or 0)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"SQL 回填当日成本失败: {e}")
        return 0.0


def daily_cost_limit() -> float:
    try:
        return float(get_config("daily_cost_limit", "1.0"))
    except Exception:
        return 1.0


def guard_active() -> bool:
    """成本超限降级是否生效（开关 && 当日成本 > 预算）"""
    if not get_config_bool("cost_guard_enabled", True):
        return False
    limit = daily_cost_limit()
    if limit <= 0:
        return False
    current = daily_cost()
    over = current >= limit
    if over:
        logger.info(f"成本超限降级触发: 当日 {current:.4f} >= 预算 {limit}")
    return over
