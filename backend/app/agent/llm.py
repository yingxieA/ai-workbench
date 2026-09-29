"""模型降级链：qwen-max → qwen-plus → deepseek-chat

- 统一使用 langchain-openai ChatOpenAI（OpenAI 兼容端点），LangSmith 自动追踪每个调用
- 超时/报错自动切换下一模型，记录 fallback_triggered + error_type
- DeepSeek key 未配置时自动跳过该档
"""

import time

from langchain_openai import ChatOpenAI
from langsmith import traceable

from app.config import settings
from app.services import cost_service
from app.utils.logger import get_logger

logger = get_logger("llm_fallback")

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_URL = "https://api.deepseek.com/v1"

# 模型链配置（可扩展：后续挪到 yaml/数据库热更）
MODEL_CHAIN = [
    {"name": "qwen-max", "base_url": DASHSCOPE_URL, "key": lambda: settings.DASHSCOPE_API_KEY, "timeout": 15},
    {"name": "qwen-plus", "base_url": DASHSCOPE_URL, "key": lambda: settings.DASHSCOPE_API_KEY, "timeout": 15},
    {"name": "deepseek-chat", "base_url": DEEPSEEK_URL, "key": lambda: settings.DEEPSEEK_API_KEY, "timeout": 20},
]


def _active_chain() -> list[dict]:
    """成本超限降级：guard_active 时跳过最高档模型（从第 2 档开始），返回生效链"""
    try:
        if cost_service.guard_active():
            if len(MODEL_CHAIN) > 1:
                logger.info(f"成本超限降级: 跳过 {MODEL_CHAIN[0]['name']}，从 {MODEL_CHAIN[1]['name']} 开始")
                return list(MODEL_CHAIN[1:])
    except Exception as e:
        logger.warning(f"成本守卫检查失败（放行）: {e}")
    return list(MODEL_CHAIN)


def _build_client(cfg: dict) -> ChatOpenAI:
    return ChatOpenAI(
        model=cfg["name"],
        api_key=cfg["key"](),
        base_url=cfg["base_url"],
        timeout=cfg["timeout"],
        max_retries=0,  # 不自动重试：失败直接交给降级链
        temperature=0.1,
        streaming=True,
    )


def _error_type(e: Exception) -> str:
    msg = str(e)
    if "timed out" in msg or "timeout" in msg.lower() or isinstance(e, TimeoutError):
        return "timeout"
    if "authentication" in msg.lower() or "api key" in msg.lower() or "401" in msg:
        return "auth_error"
    if "rate" in msg.lower() or "429" in msg:
        return "rate_limit"
    if "model_not_found" in msg or "model" in msg.lower() and "not found" in msg:
        return "model_not_found"
    return type(e).__name__


@traceable(name="llm_chain_complete")
def chat_complete(messages: list[dict]) -> tuple[str, dict]:
    """非流式调用，带降级链。返回 (text, info)"""
    info: dict = {"fallback_triggered": False, "error_type": None, "from_model": None, "to_model": None}
    for i, cfg in enumerate(_active_chain()):
        key = cfg["key"]()
        if not key:
            logger.info(f"跳过模型 {cfg['name']}（未配置 key）")
            continue
        t0 = time.time()
        try:
            client = _build_client(cfg)
            resp = client.invoke(messages)
            info["model_used"] = cfg["name"]
            info["fallback_triggered"] = i > 0
            info["latency_ms"] = int((time.time() - t0) * 1000)
            if i > 0:
                info["from_model"] = _active_chain()[i - 1]["name"]
                info["to_model"] = cfg["name"]
            # 成本：非流式取 usage，无 usage 用字符近似
            try:
                usage = getattr(resp, "usage", None)
                tokens = usage.total_tokens if (usage and usage.total_tokens) else len(resp.content or "")
            except Exception:
                tokens = len(resp.content or "")
            info["token_usage"] = tokens
            info["cost"] = cost_service.add_cost(cfg["name"], tokens)
            return resp.content, info
        except Exception as e:
            info["error_type"] = _error_type(e)
            logger.warning(f"模型 {cfg['name']} 调用失败，降级: {e}")
    raise RuntimeError(f"模型链全部不可用: {info}")


def chat_stream(messages: list[dict]) -> tuple[object, dict]:
    """流式调用，带降级链。返回 (generator_yield_token, info)"""
    info: dict = {"fallback_triggered": False, "error_type": None, "from_model": None, "to_model": None}
    for i, cfg in enumerate(_active_chain()):
        key = cfg["key"]()
        if not key:
            continue
        t0 = time.time()
        try:
            client = _build_client(cfg)
            stream = client.stream(messages)
            # 首 token 前失败（连接/鉴权/超时）→ 切下一模型；成功后进入生成器
            first = next(stream)
            info["model_used"] = cfg["name"]
            info["fallback_triggered"] = i > 0
            if i > 0:
                info["from_model"] = _active_chain()[i - 1]["name"]
                info["to_model"] = cfg["name"]

            def gen():
                nonlocal info
                yield first.content or ""
                total = len(first.content or "")
                for chunk in stream:
                    c = chunk.content or ""
                    if c:
                        total += len(c)
                        yield c
                info["latency_ms"] = int((time.time() - t0) * 1000)
                info["token_usage"] = total
                info["cost"] = cost_service.add_cost(cfg["name"], total)

            return gen(), info
        except Exception as e:
            info["error_type"] = _error_type(e)
            logger.warning(f"模型 {cfg['name']} 首 token 失败，降级: {e}")
    raise RuntimeError(f"模型链全部不可用: {info}")
