"""工具执行节点：LLM 生成参数 → jsonschema 校验 → 高风险人工确认 → 执行 → 结果回流

- 参数生成：对每个工具用 LLM 结构化输出 JSON 参数（兼容降级链所有模型，不依赖各家 function calling）
- 校验：jsonschema 拦截参数注入；校验失败不执行、结果带 error 回流
- 高风险（run_code / http_request / 外部写类工具）：interrupt 人工确认，确认后从断点继续
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from app.agent.llm import achat_complete, achat_complete_light, chat_complete, chat_complete_light
from app.agent.state import AgentState
from app.agent.tools.registry import ToolContext, registry, RISK_HIGH
from app.agent.tools.executor import aexecute_tool
from app.agent.tools.validation import safe_truncate, validate_arguments
from app.agent.cancels import is_cancelled, make_cancel_check, run_cancellable
from app.agent.loop import get_tool_timeout
from app.utils.logger import get_logger

logger = get_logger("tool_execute_node")

ARGS_PROMPT = """你是工具参数生成器。根据用户问题与工具定义，为指定工具生成合法的 JSON 参数。

工具：{name}
用途：{description}
参数 Schema：{schema}

用户问题：{question}

要求：
1. 只输出 JSON 参数对象，不要任何其他文字（不要 ```json 包裹）
2. 参数必须符合 Schema（必填项必须有、类型正确、enum 取合法值）
3. 无法从问题推断的可选参数省略
4. 用户问题明显不需要该工具时，输出 {{"__skip__": true}}
"""


def _parse_args(text: str) -> dict:
    if not text:
        return {}
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _call_args_llm(fn, timeout: float, sid: str = "", epoch: int | None = None):
    """LLM 直调包可取消分片轮询（P2）：超时/取消返回错误结构 → 抛异常由调用方降级空参。
    正常返回 fn 的 (text, info) tuple；run_cancellable 超时/取消分支返回 {"success": False, ...}。
    """
    r = run_cancellable(fn, timeout, f"参数生成超时（>{timeout}s）", sid=sid, epoch=epoch)
    if isinstance(r, dict) and r.get("success") is False:
        raise TimeoutError(str(r.get("error") or "参数生成超时"))
    return r


async def _agenerate_args(name: str, spec, question: str, sid: str = "", epoch: int | None = None) -> dict:
    """async 参数生成（方案 B）：achat_complete_light + wait_for 协程级真杀；
    行为与原版 _generate_args 完全对齐（spec.parameters / 单 user 消息 / 失败回退主链一次）"""
    prompt = ARGS_PROMPT.format(
        name=name,
        description=spec.description,
        schema=json.dumps(spec.parameters, ensure_ascii=False),
        question=question,
    )
    try:
        text, _ = await asyncio.wait_for(
            achat_complete_light(
                [{"role": "user", "content": prompt}],
                timeout=12,
                sid=sid,
                epoch=epoch,
            ),
            timeout=12,
        )
        args = _parse_args(text)
        if isinstance(args, dict):
            return args
    except asyncio.TimeoutError:
        logger.warning(f"工具 {name} 参数生成超时（12s），回退主链")
    except asyncio.CancelledError:
        raise  # 方案 B：取消直接冒泡
    except Exception as e:
        logger.warning(f"轻量模型参数生成失败 {name}，回退主链: {e}")
    try:
        # 轻量模型不可用/超时 → 回退主链一次（与同步版一致），仍失败才空参由校验兜底
        text, _ = await asyncio.wait_for(
            achat_complete(
                [{"role": "user", "content": prompt}],
                timeout=20,
                sid=sid,
                epoch=epoch,
            ),
            timeout=20,
        )
        args = _parse_args(text)
        return args if isinstance(args, dict) else {}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"参数生成失败 {name}（降级空参数由校验兜底）: {e}")
        return {}


def _generate_args(name: str, spec, question: str, sid: str = "", epoch: int | None = None) -> dict:
    """轻量模型结构化生成工具参数（失败降级为空 dict，由 jsonschema 校验兜底）：
    - 用 chat_complete_light（默认 qwen-turbo，配置 llm_args_model 可改），简单结构化任务不必动用主链重模型
    - 独立短超时（12s）：避免 qwen-max 偶发 15s+ 拖垮工具链路（用户侧表现"一直思考中、0 次工具调用"）
    """
    prompt = ARGS_PROMPT.format(
        name=name,
        description=spec.description,
        schema=json.dumps(spec.parameters, ensure_ascii=False),
        question=question,
    )
    try:
        text, _ = _call_args_llm(lambda: chat_complete_light([{"role": "user", "content": prompt}]), 12, sid, epoch)
        args = _parse_args(text)
        if isinstance(args, dict):
            return args
    except Exception as e:
        logger.warning(f"轻量模型参数生成失败 {name}，回退主链: {e}")
    try:
        # 轻量模型不可用/超时 → 回退主链一次（qwen-max→qwen-plus→deepseek），仍失败才空参由校验兜底
        text, _ = _call_args_llm(lambda: chat_complete([{"role": "user", "content": prompt}]), 20, sid, epoch)
        args = _parse_args(text)
        return args if isinstance(args, dict) else {}
    except Exception as e:
        logger.warning(f"参数生成失败 {name}（降级空参数由校验兜底）: {e}")
        return {}


async def tool_execute(state: AgentState) -> AgentState:
    """顺序执行工具：每个工具独立 参数生成→校验→(高风险中断)→执行"""
    _node_t0 = time.time()
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()

    tools = state.get("tool_calls") or []
    question = state.get("question", "")
    ctx = ToolContext(
        user_id=state.get("user_id"),
        session_id=state.get("session_id"),
        question=question,
        epoch=state.get("epoch"),
        cancel_check=make_cancel_check(state.get("session_id", ""), state.get("epoch")),
    )
    logger.info(f"工具执行节点开始: tools={tools} n={len(tools)}")

    results: list[dict] = []
    seen: set[str] = set()
    _sid = state.get("session_id", "")
    for name in tools:
        # 打断检查：新请求到达（打断模式标记）→ 终止工具循环，尽快释放会话锁
        if is_cancelled(_sid, state.get("epoch")):
            logger.info(f"工具执行被新请求打断，终止工具循环: session={_sid}")
            break
        if name in seen:
            results.append(
                {"success": False, "tool": name, "result": None, "error": "重复工具调用已跳过（同一步只执行一次）"}
            )
            _push_result(writer, name, False, None, "重复调用已跳过")
            logger.info(f"重复工具调用去重: {name}")
            continue
        seen.add(name)
        spec = registry.get(name)
        if spec is None:
            results.append({"success": False, "tool": name, "result": None, "error": f"工具不存在: {name}"})
            _push_result(writer, name, False, None, f"工具不存在: {name}")
            continue

        # 终极防线（不依赖路由/参数生成是否正确）：联网开关关闭时，联网类工具一律不执行
        _opts = state.get("options") or {}
        if not bool(_opts.get("web_search", True)) and name in ("web_search", "web_fetch", "http_request"):
            logger.info(f"联网搜索开关关闭，跳过联网工具: {name}")
            results.append({"success": False, "tool": name, "result": None, "error": "联网搜索已关闭，工具已跳过"})
            _push_result(writer, name, False, None, "联网搜索已关闭，已跳过")
            continue

        from app.agent.tracing import emit as _trace_emit

        # 参数生成阶段先推 thinking（避免 LLM 生成参数期间前端"0 次工具调用"空等）
        try:
            _push(
                writer,
                {
                    "type": "thinking",
                    "step_kind": "action",
                    "tool": name,
                    "content": f"正在准备调用工具「{name}」…",
                    "url": None,
                },
            )
        except Exception:
            pass
        args = await _agenerate_args(name, spec, question, sid=_sid, epoch=state.get("epoch"))
        _trace_emit(state, "tool_call", {"tool": name, "args": args})
        if args.get("__skip__"):
            results.append({"success": False, "tool": name, "result": None, "error": "工具不适用于当前问题（已跳过）"})
            _push_result(writer, name, False, None, "跳过：当前问题不需要该工具")
            continue

        ok, err = validate_arguments(spec, args)
        if not ok:
            results.append({"success": False, "tool": name, "result": None, "error": err})
            _push_result(writer, name, False, None, err)
            logger.warning(f"工具 {name} 参数注入被拦截: {err}")
            continue

        # 高风险工具：人工确认（interrupt 保存断点，resume 后从返回值继续）
        if spec.risk_level == RISK_HIGH:
            try:
                _push(
                    writer,
                    {
                        "type": "tool_confirm",
                        "tool": name,
                        "args": args,
                        "risk_level": "high",
                        "description": spec.description,
                    },
                )
                # 首次执行：interrupt 抛 GraphInterrupt 让图暂停（保存 checkpoint）；
                # resume 后：返回用户决策值。绝不能吞掉 GraphInterrupt，否则确认不生效
                decision = interrupt({"type": "tool_confirm", "tool": name, "args": args, "risk_level": "high"})
                approved = bool(decision and decision.get("approved"))
            except GraphInterrupt:
                raise  # 交还 LangGraph 处理暂停
            except Exception as e:
                logger.error(f"interrupt 失败 {name}: {e}")
                approved = False
            if not approved:
                results.append(
                    {"success": False, "tool": name, "result": None, "error": "用户拒绝了该操作", "rejected": True}
                )
                _push_result(writer, name, False, None, "用户拒绝了该操作")
                continue

        # 执行前推进度事件：语义化（带 query/url），对齐 DeepSeek「搜索…/打开…页面」，不刷工具 schema 描述
        try:
            _args_txt = args or {}
            if name == "web_search":
                _q = _args_txt.get("query") or ""
                _content = f"正在搜索：「{_q}」" if _q else "正在联网搜索"
            elif name == "web_fetch":
                _url = str(_args_txt.get("url") or "")[:48]
                _content = f"正在打开网页获取内容：{_url}"
            elif name == "http_request":
                _content = (
                    f"正在请求 {str(_args_txt.get('method') or 'GET').upper()} {str(_args_txt.get('url') or '')[:40]}"
                )
            else:
                _content = f"正在调用 {name}"
            _push(writer, {"type": "thinking", "content": _content})
        except Exception:
            pass
        _tool_t0 = time.time()
        logger.info(f"工具执行开始: {name} args={json.dumps(args, ensure_ascii=False)[:120]}")
        try:
            r = await asyncio.wait_for(aexecute_tool(name, args, ctx), timeout=get_tool_timeout())
        except asyncio.TimeoutError:
            r = {"success": False, "tool": name, "result": None, "error": f"工具执行超时（>{get_tool_timeout()}s）"}
        except asyncio.CancelledError:
            raise  # 方案 B：取消直接冒泡
        logger.info(
            f"工具执行完成: {name} success={r.get('success')} latency={int((time.time() - _tool_t0) * 1000)}ms"
            + (f" error={str(r.get('error'))[:120]}" if not r.get("success") else "")
        )
        results.append(r)
        _trace_emit(
            state,
            "tool_result",
            {
                "tool": name,
                "success": bool(r.get("success")),
                "error": r.get("error"),
                "result_summary": (str(r.get("result"))[:300] if r.get("success") else ""),
            },
        )
        # 搜索/浏览类工具成功后实时推进度（对齐 DeepSeek「搜索到 N 个网页 / 已浏览」）
        if r["success"]:
            _push_progress(writer, name, r.get("result"))
        _push_result(writer, name, r["success"], r.get("result"), r.get("error"))

        # P0.3 浏览正文：web_search 成功 → 自动打开前 2 条最有价值的页面抓正文（DeepSeek「浏览了 M 个页面」）
        if name == "web_search" and r.get("success"):
            rr = r.get("result") or {}
            top = (rr.get("results") or [])[:2]
            if top:
                try:
                    _push(
                        writer,
                        {
                            "type": "thinking",
                            "step_kind": "action",
                            "tool": "web_search",
                            "content": f"搜索结果含关键信息，将打开 {len(top)} 个页面浏览正文",
                            "url": None,
                        },
                    )
                except Exception:
                    pass
                for it in top:
                    _url = (it.get("url") or "").strip()
                    if not _url or _is_anti_crawl(_url):
                        continue  # 已知反爬站点（知乎等）：跳过，不做无意义抓取
                    try:
                        _push(
                            writer,
                            {
                                "type": "thinking",
                                "step_kind": "action",
                                "tool": "web_fetch",
                                "content": f"正在打开网页获取内容：{_url[:48]}",
                                "url": _url,
                            },
                        )
                    except Exception:
                        pass
                    try:
                        fr = await asyncio.wait_for(
                            aexecute_tool("web_fetch", {"url": _url, "max_chars": 4000}, ctx),
                            timeout=get_tool_timeout(),
                        )
                    except asyncio.TimeoutError:
                        fr = {
                            "success": False,
                            "tool": "web_fetch",
                            "result": None,
                            "error": f"网页浏览超时（>{get_tool_timeout()}s）",
                        }
                    except asyncio.CancelledError:
                        raise
                    fr["tool"] = "web_fetch"
                    fr["auto"] = True  # 标记为自动补充的浏览步骤（生成端合并引用来源用）
                    # 失败不 append 到 tool_results（避免污染引用来源/生成端看到"抓取失败"刷屏）；仅发 trace 可观测
                    if fr.get("success"):
                        results.append(fr)
                        _push_progress(writer, "web_fetch", fr.get("result"))
                        _push_result(writer, "web_fetch", True, fr.get("result"), None)
                    _trace_emit(
                        state,
                        "tool_result",
                        {
                            "tool": "web_fetch",
                            "success": bool(fr.get("success")),
                            "error": fr.get("error"),
                            "result_summary": (str(fr.get("result"))[:200] if fr.get("success") else ""),
                            "auto": True,
                        },
                    )
                    if is_cancelled(_sid, state.get("epoch")):
                        logger.info("网页浏览错峰被打断（取消）")
                        break
                    await asyncio.sleep(0.2)  # B5：async sleep 可被 task.cancel 即时中断

    # 工具结果 → 引用来源（web_search / web_fetch / http_request 带 url 的结果进前端「引用来源」Drawer）
    web_sources = build_web_sources(results)
    logger.info(
        f"工具执行节点完成: tools={tools} results={len(results)} latency={int((time.time() - _node_t0) * 1000)}ms"
    )
    if web_sources:
        try:
            _push(writer, {"type": "contexts", "contexts_meta": web_sources})
        except Exception:
            pass
        return {"tool_results": results, "tool_confirm_pending": False, "contexts_meta": web_sources}

    return {"tool_results": results, "tool_confirm_pending": False}


def _push(writer, payload: dict) -> None:
    try:
        writer(payload)
    except Exception:
        pass  # 无流式消费者时忽略


def _push_progress(writer, tool: str, result) -> None:
    """搜索/浏览类工具成功后实时推进度 thinking（与 loop.py 同构）"""
    if tool not in ("web_search", "web_fetch", "http_request"):
        return
    try:
        rr = result or {}
        if isinstance(rr, dict) and isinstance(rr.get("results"), list):
            n = len(rr["results"])
            # 动作轨迹：结构化事件（step_kind=action），前端思考卡内以「动作行」展示并可溯源
            _push(
                writer,
                {
                    "type": "thinking",
                    "step_kind": "action",
                    "tool": tool,
                    "content": f"已搜索到 {n} 个网页",
                    "url": None,
                },
            )
        elif isinstance(rr, dict) and (rr.get("url") or rr.get("title")):
            title = str(rr.get("title") or rr.get("url") or "")[:60]
            _push(
                writer,
                {
                    "type": "thinking",
                    "step_kind": "action",
                    "tool": tool,
                    "content": f"已浏览：{title}",
                    "url": rr.get("url"),
                },
            )
    except Exception:
        pass


# 已知强反爬站点：自动浏览正文直接跳过（web_fetch 即使带 UA 也 403，失败刷屏无意义）
_ANTI_CRAWL_HOSTS = {
    "zhihu.com",
    "weibo.com",
    "xiaohongshu.com",
    "douyin.com",
    "bilibili.com",
    "toutiao.com",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "x.com",
    "twitter.com",
}


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).netloc or "").lower()
    for p in ("www.", "m.", "cn.", "news.", "wap."):
        if host.startswith(p):
            host = host[len(p) :]
    return host


def _is_anti_crawl(url: str) -> bool:
    h = _host_of(url)
    return any(h == k or h.endswith("." + k) for k in _ANTI_CRAWL_HOSTS)


_DATE_PATTERN = re.compile(r"(20\d{2}[./\-\u5e74]\d{1,2}[./\-\u6708]\d{1,2})")


def _extract_site(url: str) -> str:
    """从 url 提取站点名（去 www. 等前缀）"""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).netloc or "").lower()
        for p in ("www.", "m.", "cn.", "news.", "wap."):
            if host.startswith(p):
                host = host[len(p) :]
        return host or ""
    except Exception:
        return ""


def _extract_date(title: str, snippet: str) -> str:
    """从标题/摘要提取日期（2026/09/28 → 2026/09/28），无则空"""
    for txt in (snippet or "", title or ""):
        m = _DATE_PATTERN.search(txt)
        if m:
            s = (
                m.group(1)
                .replace("年", "/")
                .replace("月", "/")
                .replace("日", "")
                .replace(".", "/")
                .replace("-", "/")
                .strip("/")
            )
            parts = [p.zfill(2) for p in s.split("/")]
            if (
                len(parts) == 3
                and 1900 <= int(parts[0]) <= 2100
                and 1 <= int(parts[1]) <= 12
                and 1 <= int(parts[2]) <= 31
            ):
                return f"{parts[0]}/{parts[1]}/{parts[2]}"
    return ""


def build_web_sources(results: list[dict]) -> list[dict]:
    """工具结果 → 引用来源（web_search / web_fetch / http_request 带 url 的结果）

    对齐 DeepSeek 引用样式：每条含 站点名 / 日期 / 标题 / 内容摘要 / favicon。
    """
    web_sources: list[dict] = []
    for r in results:
        if not (r.get("success") and isinstance(r.get("result"), dict)):
            continue
        rr = r["result"]
        items: list[dict] = []
        if isinstance(rr.get("results"), list):
            # web_search：results = [{title, url, snippet?}]
            items = [
                {"title": it.get("title"), "url": it.get("url"), "content": it.get("snippet") or ""}
                for it in rr["results"]
            ]
        elif rr.get("url") or rr.get("title"):
            # web_fetch / http_request：{title, url, content|text}
            items = [
                {"title": rr.get("title"), "url": rr.get("url"), "content": rr.get("content") or rr.get("text") or ""}
            ]
        for it in items:
            if not it.get("url"):
                continue
            title = safe_truncate(str(it.get("title") or it.get("url")), 120)
            content = safe_truncate(str(it.get("content") or ""), 600)
            site = _extract_site(it.get("url"))
            web_sources.append(
                {
                    "index": len(web_sources) + 1,
                    "title": title,
                    "content": content,
                    "url": it.get("url"),
                    "site": site,
                    "date": _extract_date(title, content),
                    "favicon": f"https://{site}/favicon.ico" if site else "",
                    "source": "web",
                }
            )
    return web_sources


def _push_result(writer, name: str, success: bool, result, error: str | None) -> None:
    summary = ""
    if success:
        r = result or {}
        if isinstance(r, dict):
            # web_search 等带 results 列表的工具：压缩为条数摘要，明细交给前端「引用来源」Drawer，避免卡片堆链接
            if isinstance(r.get("results"), list):
                summary = f"已检索到 {len(r['results'])} 条结果（详见引用来源）"
            else:
                summary = r.get("text") or r.get("result") or r.get("stdout") or r.get("body") or ""
            if not summary:
                # 结果全空（如 run_code 无 print 输出）→ 友好提示；否则兜底 JSON 摘要
                if all(v in (None, "", False, [], {}) for v in r.values()):
                    summary = "执行成功（无输出）"
                else:
                    summary = json.dumps(r, ensure_ascii=False)[:300]
        else:
            summary = str(r)[:300]
    _push(
        writer,
        {
            "type": "tool_result",
            "tool": name,
            "success": success,
            "summary": safe_truncate(summary or "", 600),
            "error": error,
        },
    )
