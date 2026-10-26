"""模型降级链：qwen-max → qwen-plus → deepseek-chat

- 统一使用 langchain-openai ChatOpenAI（OpenAI 兼容端点），LangSmith 自动追踪每个调用
- 超时/报错自动切换下一模型，记录 fallback_triggered + error_type
- DeepSeek key 未配置时自动跳过该档
- 模型链 / 温度已配置化：system_configs 表（llm_model_chain / llm_temperature），热更新
"""

import json
import time

from langchain_openai import ChatOpenAI
from langsmith import traceable

from app.config import settings
from app.services import cost_service
from app.services.system_configs import get_config
from app.agent.cancels import is_cancelled
from app.utils.logger import get_logger

logger = get_logger("llm_fallback")

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_URL = "https://api.deepseek.com/v1"

# 默认模型链（配置缺失/解析失败时回退；可通过 system_configs.llm_model_chain 热更新覆盖）
MODEL_CHAIN = [
    {"name": "qwen-max", "base_url": DASHSCOPE_URL, "key": lambda: settings.DASHSCOPE_API_KEY, "timeout": 10},
    {"name": "qwen-plus", "base_url": DASHSCOPE_URL, "key": lambda: settings.DASHSCOPE_API_KEY, "timeout": 10},
    {"name": "deepseek-chat", "base_url": DEEPSEEK_URL, "key": lambda: settings.DEEPSEEK_API_KEY, "timeout": 12},
]

DEFAULT_TEMPERATURE = 0.1


def _load_model_chain() -> list[dict]:
    """从 system_configs 读模型链（JSON 数组），解析失败回退默认"""
    raw = get_config("llm_model_chain", "")
    if raw:
        try:
            arr = json.loads(raw)
            if isinstance(arr, list) and arr:
                chain = []
                for c in arr:
                    if not (c.get("name") and c.get("base_url")):
                        continue
                    key_env = c.get("api_key_env", "")
                    chain.append(
                        {
                            "name": str(c["name"]),
                            "base_url": str(c["base_url"]),
                            "key": (lambda n=key_env: getattr(settings, n, "") if n else ""),
                            "timeout": int(c.get("timeout", 15)),
                        }
                    )
                if chain:
                    return chain
        except Exception as e:
            logger.warning(f"llm_model_chain 配置解析失败，回退默认链: {e}")
    return list(MODEL_CHAIN)


def _load_temperature() -> float:
    """从 system_configs 读温度，解析失败回退默认 0.1"""
    raw = get_config("llm_temperature", "")
    if raw:
        try:
            v = float(raw)
            if 0.0 <= v <= 2.0:
                return v
        except Exception:
            pass
    return DEFAULT_TEMPERATURE


def _active_chain() -> list[dict]:
    """成本超限降级：guard_active 时跳过最高档模型（从第 2 档开始），返回生效链"""
    chain = _load_model_chain()
    try:
        if cost_service.guard_active():
            if len(chain) > 1:
                logger.info(f"成本超限降级: 跳过 {chain[0]['name']}，从 {chain[1]['name']} 开始")
                return list(chain[1:])
    except Exception as e:
        logger.warning(f"成本守卫检查失败（放行）: {e}")
    return list(chain)


def _build_client(cfg: dict) -> ChatOpenAI:
    return ChatOpenAI(
        model=cfg["name"],
        api_key=cfg["key"](),
        base_url=cfg["base_url"],
        timeout=cfg["timeout"],
        max_retries=0,  # 不自动重试：失败直接交给降级链
        temperature=_load_temperature(),
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
    chain = _active_chain()
    for i, cfg in enumerate(chain):
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
                info["from_model"] = chain[i - 1]["name"]
                info["to_model"] = cfg["name"]
            # 成本：非流式取 usage，无 usage 用字符近似
            try:
                usage = getattr(resp, "usage", None)
                tokens = usage.total_tokens if (usage and usage.total_tokens) else len(resp.content or "")
            except Exception:
                tokens = len(resp.content or "")
            info["token_usage"] = tokens
            info["cost"] = cost_service.add_cost(cfg["name"], tokens)
            logger.info(
                f"模型调用成功: model={cfg['name']} latency={info['latency_ms']}ms "
                f"tokens={tokens} fallback={info['fallback_triggered']}"
                + (f" from={chain[i - 1]['name']}" if i > 0 else "")
            )
            return resp.content, info
        except Exception as e:
            info["error_type"] = _error_type(e)
            logger.warning(f"模型 {cfg['name']} 调用失败，降级: err={info['error_type']} {e}")
        raise RuntimeError(f"模型链全部不可用: {info}")


def chat_complete_light(messages: list[dict], timeout: int = 12) -> tuple[str, dict]:
    """轻量模型调用（工具参数生成等简单结构化任务）：
    - 模型从 system_configs.llm_args_model 读（默认 qwen-turbo，dashscope 通义端点）
    - 独立短超时（默认 12s）：避免主链重模型（qwen-max）偶发 15s+ 拖垮工具链路
    - 失败抛异常，由调用方降级（参数生成失败 → 空参数，jsonschema 校验兜底）
    """
    model = (get_config("llm_args_model", "") or "").strip() or "qwen-turbo"
    client = ChatOpenAI(
        model=model,
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_URL,
        timeout=timeout,
        max_retries=0,
        temperature=0.0,  # 参数生成要确定性，温度 0
        streaming=False,
    )
    t0 = time.time()
    resp = client.invoke(messages)
    tokens = 0
    try:
        usage = getattr(resp, "usage", None)
        tokens = usage.total_tokens if (usage and usage.total_tokens) else len(resp.content or "")
    except Exception:
        tokens = len(resp.content or "")
    info = {"model_used": model, "latency_ms": int((time.time() - t0) * 1000), "token_usage": tokens}
    logger.info(f"轻量模型调用成功: model={model} latency={info['latency_ms']}ms tokens={tokens}")
    return resp.content or "", info


def chat_stream_vl(messages: list[dict], session_id: str = "", epoch: int | None = None) -> tuple[object, dict]:
    """流式调用（多模态图文生成）：固定走视觉模型 vision_generate_model（默认 qwen-vl-max）。
    与文本链解耦：图片问答场景才用，文本场景不受影响；失败抛异常由调用方降级回文本链。"""
    model = "qwen-vl-max"
    try:
        from app.services.system_configs import get_config as _gc

        model = str(_gc("vision_generate_model", "qwen-vl-max")).strip() or "qwen-vl-max"
    except Exception:
        pass
    client = ChatOpenAI(
        model=model,
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_URL,
        timeout=30,
        max_retries=0,
        temperature=_load_temperature(),
        streaming=True,
    )
    t0 = time.time()
    info: dict = {"fallback_triggered": False, "error_type": None}
    try:
        resp = client.stream(messages)
        info["model_used"] = model
        info["latency_ms"] = int((time.time() - t0) * 1000)

        def gen():
            buf = []
            for chunk in resp:
                # P0 取消传播：被打断（新请求/停止）→ 停止生成，立即收尾
                if session_id and is_cancelled(session_id, epoch):
                    break
                tok = getattr(chunk, "content", "") or ""
                buf.append(tok)
                yield tok
            full = "".join(buf)
            info["token_usage"] = len(full)
            try:
                info["cost"] = cost_service.add_cost(model, len(full))
            except Exception:
                pass

        return gen, info
    except Exception as e:
        info["error_type"] = _error_type(e)
        logger.warning(f"视觉模型 {model} 调用失败（降级回文本链）: {e}")
        raise


def _extract_reasoning(chunk) -> str:
    """从流式 chunk 提取推理内容（reasoning_content）。

    推理模型（deepseek-reasoner / qwen3 思考模式等）在 OpenAI 兼容流式响应里，
    推理文本与正文分离：langchain-openai 一般映射到 additional_kwargs["reasoning_content"]，
    部分版本放在 response_metadata；两条路径都兜底探测，命中即返回。
    """
    try:
        ak = getattr(chunk, "additional_kwargs", None) or {}
        r = ak.get("reasoning_content") or ak.get("reasoning") or ""
        if r:
            return r
    except Exception:
        pass
    try:
        rm = getattr(chunk, "response_metadata", None) or {}
        r = rm.get("reasoning_content") or (rm.get("message") or {}).get("reasoning_content") or ""
        return r or ""
    except Exception:
        return ""


def chat_stream(
    messages: list[dict], on_reasoning=None, session_id: str = "", epoch: int | None = None
) -> tuple[object, dict]:
    """流式调用，带降级链。返回 (generator_yield_token, info)。

    on_reasoning: 可选回调，透传推理模型流式输出的 reasoning_content 片段（全文逐片回调）。
    推理模型未命中时回调不会被调用，正文流不受影响。
    """
    info: dict = {"fallback_triggered": False, "error_type": None, "from_model": None, "to_model": None}
    chain = _active_chain()
    for i, cfg in enumerate(chain):
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
                info["from_model"] = chain[i - 1]["name"]
                info["to_model"] = cfg["name"]

            def gen():
                nonlocal info
                yield first.content or ""
                total = len(first.content or "")
                if on_reasoning:
                    r = _extract_reasoning(first)
                    if r:
                        try:
                            on_reasoning(r)
                        except Exception:
                            pass
                for chunk in stream:
                    # P0 取消传播：被打断（新请求/停止）→ 停止生成，立即收尾
                    if session_id and is_cancelled(session_id, epoch):
                        break
                    c = chunk.content or ""
                    if c:
                        total += len(c)
                        yield c
                    if on_reasoning:
                        r = _extract_reasoning(chunk)
                        if r:
                            try:
                                on_reasoning(r)
                            except Exception:
                                pass
                info["latency_ms"] = int((time.time() - t0) * 1000)
                info["token_usage"] = total
                info["cost"] = cost_service.add_cost(cfg["name"], total)
                logger.info(
                    f"模型流式完成: model={cfg['name']} latency={info['latency_ms']}ms "
                    f"chars={total} fallback={info['fallback_triggered']}"
                    + (f" from={chain[i - 1]['name']}" if i > 0 else "")
                )

            first_token_ms = int((time.time() - t0) * 1000)
            logger.info(
                f"模型流式首token: model={cfg['name']} first_token={first_token_ms}ms "
                f"fallback={info['fallback_triggered']}"
            )
            return gen(), info
        except Exception as e:
            info["error_type"] = _error_type(e)
            logger.warning(f"模型 {cfg['name']} 首 token 失败，降级: err={info['error_type']} {e}")
    raise RuntimeError(f"模型链全部不可用: {info}")


# ── B2：async 版（方案 B 真取消：ainvoke/astream + asyncio.wait_for 协程级强杀）──
# 与同步版并存：async 节点（生产图）走 achat_*，eval_runner/脚本等同步上下文继续用 chat_*。


async def achat_complete(messages: list[dict], sid: str = "", epoch: int | None = None) -> tuple[str, dict]:
    """非流式 async，带降级链。与 chat_complete 同语义，差异：
    - client.ainvoke 包 asyncio.wait_for → 超时/取消可协程级强杀（不再等线程自然结束）
    - 每次调用前 is_cancelled 检查：取消态不发起请求，抛 CancelledError 由调用方收尾
    """
    import asyncio

    info: dict = {"fallback_triggered": False, "error_type": None, "from_model": None, "to_model": None}
    chain = _active_chain()
    for i, cfg in enumerate(chain):
        key = cfg["key"]()
        if not key:
            logger.info(f"跳过模型 {cfg['name']}（未配置 key）")
            continue
        if sid and is_cancelled(sid, epoch):
            raise asyncio.CancelledError("已取消")
        t0 = time.time()
        try:
            client = _build_client(cfg)
            try:
                resp = await asyncio.wait_for(client.ainvoke(messages), timeout=cfg["timeout"])
            except asyncio.TimeoutError:
                raise TimeoutError(f"模型 {cfg['name']} 调用超时（>{cfg['timeout']}s）")
            info["model_used"] = cfg["name"]
            info["fallback_triggered"] = i > 0
            info["latency_ms"] = int((time.time() - t0) * 1000)
            if i > 0:
                info["from_model"] = chain[i - 1]["name"]
                info["to_model"] = cfg["name"]
            try:
                usage = getattr(resp, "usage", None)
                tokens = usage.total_tokens if (usage and usage.total_tokens) else len(resp.content or "")
            except Exception:
                tokens = len(resp.content or "")
            info["token_usage"] = tokens
            try:
                info["cost"] = cost_service.add_cost(cfg["name"], tokens)
            except Exception:
                pass
            logger.info(
                f"模型 async 调用成功: model={cfg['name']} latency={info['latency_ms']}ms "
                f"tokens={tokens} fallback={info['fallback_triggered']}"
                + (f" from={chain[i - 1]['name']}" if i > 0 else "")
            )
            return resp.content, info
        except asyncio.CancelledError:
            raise  # 外层 wait_for/task.cancel 真杀：不吞不降级
        except Exception as e:
            info["error_type"] = _error_type(e)
            logger.warning(f"模型 {cfg['name']} async 调用失败，降级: err={info['error_type']} {e}")
    raise RuntimeError(f"模型链全部不可用: {info}")


async def achat_complete_light(
    messages: list[dict], timeout: int = 12, sid: str = "", epoch: int | None = None
) -> tuple[str, dict]:
    """轻量模型 async 调用（工具参数生成）：与 chat_complete_light 同语义，wait_for 强杀 + 取消检查"""
    import asyncio

    if sid and is_cancelled(sid, epoch):
        raise asyncio.CancelledError("已取消")
    model = (get_config("llm_args_model", "") or "").strip() or "qwen-turbo"
    client = ChatOpenAI(
        model=model,
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_URL,
        timeout=timeout,
        max_retries=0,
        temperature=0.0,
        streaming=False,
    )
    t0 = time.time()
    try:
        resp = await asyncio.wait_for(client.ainvoke(messages), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"轻量模型 {model} 调用超时（>{timeout}s）")
    tokens = 0
    try:
        usage = getattr(resp, "usage", None)
        tokens = usage.total_tokens if (usage and usage.total_tokens) else len(resp.content or "")
    except Exception:
        tokens = len(resp.content or "")
    info = {"model_used": model, "latency_ms": int((time.time() - t0) * 1000), "token_usage": tokens}
    logger.info(f"轻量模型 async 调用成功: model={model} latency={info['latency_ms']}ms tokens={tokens}")
    return resp.content or "", info


async def achat_stream(
    messages: list[dict],
    on_reasoning=None,
    session_id: str = "",
    epoch: int | None = None,
) -> tuple[object, dict]:
    """流式 async，带降级链。返回 (async_generator, info)。

    - 首 token：client.astream + asyncio.wait_for → 连接/鉴权/超时协程级强杀，失败切下一模型
    - 逐 chunk：is_cancelled 检查（取消态 break），外层 task.cancel 可整体强杀
    """
    import asyncio

    info: dict = {"fallback_triggered": False, "error_type": None, "from_model": None, "to_model": None}
    chain = _active_chain()
    for i, cfg in enumerate(chain):
        key = cfg["key"]()
        if not key:
            continue
        if session_id and is_cancelled(session_id, epoch):
            raise asyncio.CancelledError("已取消")
        t0 = time.time()
        try:
            client = _build_client(cfg)
            stream = client.astream(messages)
            try:
                first = await asyncio.wait_for(anext(stream), timeout=cfg["timeout"])
            except asyncio.TimeoutError:
                raise TimeoutError(f"模型 {cfg['name']} 首 token 超时（>{cfg['timeout']}s）")
            info["model_used"] = cfg["name"]
            info["fallback_triggered"] = i > 0
            if i > 0:
                info["from_model"] = chain[i - 1]["name"]
                info["to_model"] = cfg["name"]

            async def agen():
                nonlocal info
                yield first.content or ""
                total = len(first.content or "")
                if on_reasoning:
                    r = _extract_reasoning(first)
                    if r:
                        try:
                            on_reasoning(r)
                        except Exception:
                            pass
                async for chunk in stream:
                    if session_id and is_cancelled(session_id, epoch):
                        break
                    c = chunk.content or ""
                    if c:
                        total += len(c)
                        yield c
                    if on_reasoning:
                        r = _extract_reasoning(chunk)
                        if r:
                            try:
                                on_reasoning(r)
                            except Exception:
                                pass
                info["latency_ms"] = int((time.time() - t0) * 1000)
                info["token_usage"] = total
                try:
                    info["cost"] = cost_service.add_cost(cfg["name"], total)
                except Exception:
                    pass
                logger.info(
                    f"模型 async 流式完成: model={cfg['name']} latency={info['latency_ms']}ms "
                    f"chars={total} fallback={info['fallback_triggered']}"
                    + (f" from={chain[i - 1]['name']}" if i > 0 else "")
                )

            first_token_ms = int((time.time() - t0) * 1000)
            logger.info(
                f"模型 async 流式首token: model={cfg['name']} first_token={first_token_ms}ms "
                f"fallback={info['fallback_triggered']}"
            )
            return agen(), info
        except asyncio.CancelledError:
            raise
        except Exception as e:
            info["error_type"] = _error_type(e)
            logger.warning(f"模型 {cfg['name']} async 首 token 失败，降级: err={info['error_type']} {e}")
    raise RuntimeError(f"模型链全部不可用: {info}")


async def achat_stream_vl(
    messages: list[dict],
    session_id: str = "",
    epoch: int | None = None,
) -> tuple[object, dict]:
    """流式 async（多模态图文生成）：与 chat_stream_vl 同语义，astream + 逐 chunk 取消检查；
    失败抛异常由调用方降级回文本链。"""

    model = "qwen-vl-max"
    try:
        from app.services.system_configs import get_config as _gc

        model = str(_gc("vision_generate_model", "qwen-vl-max")).strip() or "qwen-vl-max"
    except Exception:
        pass
    client = ChatOpenAI(
        model=model,
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_URL,
        timeout=30,
        max_retries=0,
        temperature=_load_temperature(),
        streaming=True,
    )
    t0 = time.time()
    info: dict = {"fallback_triggered": False, "error_type": None}
    try:
        stream = client.astream(messages)
        info["model_used"] = model
        info["latency_ms"] = int((time.time() - t0) * 1000)

        async def agen():
            buf = []
            async for chunk in stream:
                if session_id and is_cancelled(session_id, epoch):
                    break
                tok = getattr(chunk, "content", "") or ""
                buf.append(tok)
                if tok:
                    yield tok
            full = "".join(buf)
            info["token_usage"] = len(full)
            try:
                info["cost"] = cost_service.add_cost(model, len(full))
            except Exception:
                pass

        return agen(), info
    except Exception as e:
        info["error_type"] = _error_type(e)
        logger.warning(f"视觉模型 {model} async 调用失败（降级回文本链）: {e}")
        raise
