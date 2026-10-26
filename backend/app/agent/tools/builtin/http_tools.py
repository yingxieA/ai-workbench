"""内置工具：web_search / web_fetch / http_request

- web_search：实时搜索（DuckDuckGo HTML 端点，无需 key；Bing 兜底）
  生产提示：可无缝替换为 Apify / SerpAPI 等商业检索源（settings.APIFY_API_KEY 已预留）
- web_fetch：抓取网页正文（防 SSRF + 输出截断）
- http_request：通用 REST 调用（防 SSRF + 方法白名单 + 高风险需确认）
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from app.agent.cancels import is_cancelled
from app.agent.tools.registry import ToolSpec, ToolContext, RISK_HIGH, RISK_NORMAL
from app.agent.tools.validation import (
    html_to_text,
    json_safe,
    safe_truncate,
    validate_public_url,
)
from app.utils.logger import get_logger

logger = get_logger("tool_builtin_http")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_TIMEOUT = httpx.Timeout(5.0)
_client_lock = None
_shared_client = None


def _client() -> httpx.Client:
    """模块级共享 client：连接复用，避免每次检索都重建 TCP 连接（国内网络下可显著提速）"""
    global _client_lock, _shared_client
    if _client_lock is None:
        import threading

        _client_lock = threading.Lock()
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                _shared_client = httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True)
    return _shared_client


# ---- B3 async ----
_async_client_obj = None
_async_client_loop = None


async def _async_client() -> httpx.AsyncClient:
    global _async_client_obj, _async_client_loop
    loop = asyncio.get_running_loop()
    if _async_client_obj is None or _async_client_loop is not loop:
        try:
            if _async_client_obj is not None:
                await _async_client_obj.aclose()
        except Exception:
            pass
        _async_client_obj = httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True)
        _async_client_loop = loop
    return _async_client_obj


async def _search_bing_async(query: str, max_results: int) -> list[dict]:
    import html

    c = await _async_client()
    r = await c.get("https://www.bing.com/search", params={"q": query})
    r.raise_for_status()
    items = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', r.text, re.DOTALL):
        block = m.group(0)
        hm = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not hm:
            continue
        title = re.sub(r"<[^>]+>", "", hm.group(2))
        item = {"title": safe_truncate(title, 200), "url": hm.group(1)}
        snippet_m = re.search(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        if not snippet_m:
            snippet_m = re.search(r'<div class="b_caption"[^>]*>(.*?)</div>', block, re.DOTALL)
        if not snippet_m:
            snippet_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        if snippet_m:
            item["snippet"] = safe_truncate(html.unescape(re.sub(r"<[^>]+>", "", snippet_m.group(1))).strip(), 400)
        items.append(item)
        if len(items) >= max_results:
            break
    return items


async def _search_multi_async(
    queries: list[str], per_query: int, sid: str = "", epoch: int | None = None
) -> list[dict]:
    queries = [q.strip() for q in queries if q and q.strip()][:3]
    if not queries:
        return []
    if sid and is_cancelled(sid, epoch):
        return []

    async def one(q: str) -> list[dict]:
        return await asyncio.wait_for(_search_bing_async(q, per_query), timeout=6.0)

    results = await asyncio.gather(*[one(q) for q in queries], return_exceptions=True)
    seen_url: set[str] = set()
    merged: list[dict] = []
    for q, r in zip(queries, results):
        if isinstance(r, Exception):
            logger.warning(f"多路搜索 async 一路失败({q}): {r}")
            continue
        for it in r:
            u = it.get("url") or ""
            if u and u in seen_url:
                continue
            if u:
                seen_url.add(u)
            merged.append(it)
    return merged


def _search_parallel(query: str, max_results: int) -> list[dict]:
    """Bing 单引擎检索（国内直连 cn.bing.com ~0.3s）
    注：DDG 国内 DNS 解析常卡在系统 getaddrinfo（httpx 超时无法中断），
    之前并行方案会白白拖慢 5-10s，故移除 DDG，仅用 Bing 保证稳定快速"""
    return _search_bing(query, max_results)


def _search_multi(queries: list[str], per_query: int, cancel_check=None) -> list[dict]:
    """多路并行搜索：多个 query 同时检索（对齐 DeepSeek「同时进行多项搜索」），结果按 URL 去重。

    cancel_check=P2 取消回调：每 0.3s 检查一次，命中立即返回已合并结果（未完成的线程自然结束，
    最长等 httpx 5s 超时——同步世界协作式取消的可达下限）。
    """
    import concurrent.futures

    queries = [q.strip() for q in queries if q and q.strip()][:3]  # 最多 3 路
    if not queries:
        return []
    if cancel_check and cancel_check():  # P2：取消态不发起请求
        return []
    seen_url: set[str] = set()
    merged: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(queries))) as ex:
        futures = {ex.submit(_search_bing, q, per_query): q for q in queries}
        pending = set(futures)
        while pending:
            if cancel_check and cancel_check():
                for fut in pending:
                    fut.cancel()
                break
            done, pending = concurrent.futures.wait(pending, timeout=0.3)
            for fut in done:
                q = futures[fut]
                try:
                    for it in fut.result():
                        u = it.get("url") or ""
                        if u and u in seen_url:
                            continue
                        if u:
                            seen_url.add(u)
                        merged.append(it)
                except Exception as e:
                    logger.warning(f"多路搜索一路失败({q}): {e}")
        for fut in pending:  # 取消/超时后仍有未取回的：取已完成的（阻塞至多 5s httpx 超时）
            try:
                for it in fut.result():
                    u = it.get("url") or ""
                    if u and u in seen_url:
                        continue
                    if u:
                        seen_url.add(u)
                    merged.append(it)
            except Exception:
                pass
    return merged


# ---------- web_search ----------


def _search_ddg(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo HTML 端点检索（共享 client 不可 with 关闭，多线程会互相 close）"""
    c = _client()
    r = c.post("https://html.duckduckgo.com/html/", data={"q": query})
    r.raise_for_status()
    items = []
    for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.DOTALL):
        title = re.sub(r"<[^>]+>", "", m.group(2))
        items.append({"title": safe_truncate(title, 200), "url": m.group(1)})
    for i, m in enumerate(re.finditer(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL)):
        if i < len(items):
            items[i]["snippet"] = safe_truncate(re.sub(r"<[^>]+>", "", m.group(1)), 400)
    return items[:max_results]


def _search_bing(query: str, max_results: int) -> list[dict]:
    """Bing 页面检索（DDG 失败时兜底）"""
    import html

    c = _client()
    r = c.get("https://www.bing.com/search", params={"q": query})
    r.raise_for_status()
    items = []
    # Bing 新版页面：标题 <h2><a>，摘要 <p class="b_lineclamp2"> 在标题之后。
    # 必须取整个 li.b_algo 块（旧版 group(0) 截断在 </a> 导致摘要永远匹配不到）。
    for m in re.finditer(r'<li class="b_algo".*?</li>', r.text, re.DOTALL):
        block = m.group(0)
        hm = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not hm:
            continue
        title = re.sub(r"<[^>]+>", "", hm.group(2))
        item = {"title": safe_truncate(title, 200), "url": hm.group(1)}
        # 摘要：b_lineclamp <p>（新版）→ b_caption 内任意 <p> → 块内任意 <p>
        snippet_m = re.search(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        if not snippet_m:
            snippet_m = re.search(r'<div class="b_caption"[^>]*>(.*?)</div>', block, re.DOTALL)
        if not snippet_m:
            snippet_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        if snippet_m:
            item["snippet"] = safe_truncate(html.unescape(re.sub(r"<[^>]+>", "", snippet_m.group(1))).strip(), 400)
        items.append(item)
        if len(items) >= max_results:
            break
    return items


def _web_search(args: dict, ctx: ToolContext) -> dict:
    if ctx is not None and ctx.cancel_check and ctx.cancel_check():
        return {"error": "已取消"}
    query = (args.get("query") or "").strip()
    queries = args.get("queries") or []
    max_results = min(int(args.get("max_results") or 5), 10)
    if not query and not queries:
        return {"error": "query 或 queries 不能为空"}
    try:
        _cc = (ctx.cancel_check if ctx is not None else None) or None
        if isinstance(queries, list) and queries:
            # 多路并行：每个 query 返回 max_results 条，合并去重
            items = _search_multi(queries, max_results, cancel_check=_cc)
        else:
            items = _search_parallel(query, max_results)
        if ctx is not None and ctx.cancel_check and ctx.cancel_check():
            return {"error": "已取消"}
        if not items:
            return {"results": [], "note": "未检索到结果"}
        lines = []
        for i, it in enumerate(items, 1):
            snippet = it.get("snippet", "")
            lines.append(f"{i}. {it['title']}\n   {it['url']}" + (f"\n   {snippet}" if snippet else ""))
        return {"results": items, "text": "\n".join(lines)}
    except Exception as e:
        logger.error(f"web_search 失败: {e}")
        return {"error": f"检索失败: {str(e)[:200]}"}


# ---------- web_fetch ----------


def _web_fetch(args: dict, ctx: ToolContext) -> dict:
    if ctx is not None and ctx.cancel_check and ctx.cancel_check():
        return {"error": "已取消"}
    url = (args.get("url") or "").strip()
    max_chars = min(int(args.get("max_chars") or 5000), 20000)
    ok, err = validate_public_url(url)
    if not ok:
        return {"error": err}
    try:
        c = _client()  # 共享 client：不能 with 关闭（模块级复用）
        r = c.get(url)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if "application/json" in content_type or url.endswith(".json"):
            body = json.dumps(json_safe(r.json()), ensure_ascii=False, indent=2)
            text = safe_truncate(body, max_chars)
        else:
            text = html_to_text(r.text, max_chars)
        return {"title": _extract_title(r.text), "url": url, "status": r.status_code, "content": text}
    except Exception as e:
        logger.error(f"web_fetch 失败: {url} -> {e}")
        return {"error": f"抓取失败: {str(e)[:200]}"}


def _extract_title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return safe_truncate(re.sub(r"\s+", " ", m.group(1)).strip(), 120) if m else ""


# ---------- http_request ----------

_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


def _http_request(args: dict, ctx: ToolContext) -> dict:
    if ctx is not None and ctx.cancel_check and ctx.cancel_check():
        return {"error": "已取消"}
    method = (args.get("method") or "GET").upper()
    url = (args.get("url") or "").strip()
    if method not in _METHODS:
        return {"error": f"method 仅支持 {'/'.join(_METHODS)}"}
    ok, err = validate_public_url(url)
    if not ok:
        return {"error": err}
    headers = args.get("headers") or {}
    if not isinstance(headers, dict):
        return {"error": "headers 必须是 JSON 对象"}
    body = args.get("body")
    try:
        c = _client()  # 共享 client：不能 with 关闭
        kwargs = {"headers": {k: str(v) for k, v in headers.items()}}
        if body is not None:
            kwargs["json"] = body
        r = c.request(method, url, **kwargs)
        text = r.text
        if "application/json" in r.headers.get("content-type", ""):
            try:
                text = json.dumps(json_safe(r.json()), ensure_ascii=False, indent=2)
            except Exception:
                pass
        return {
            "status": r.status_code,
            "url": url,
            "body": safe_truncate(text, 8000),
        }
    except Exception as e:
        logger.error(f"http_request 失败: {method} {url} -> {e}")
        return {"error": f"请求失败: {str(e)[:200]}"}


async def _web_search_async(args: dict, ctx: ToolContext) -> dict:
    if ctx is not None and ctx.cancel_check and ctx.cancel_check():
        return {"error": "已取消"}
    query = (args.get("query") or "").strip()
    queries = args.get("queries") or []
    max_results = min(int(args.get("max_results") or 5), 10)
    if not query and not queries:
        return {"error": "query 或 queries 不能为空"}
    try:
        _sid = (ctx.session_id or "") if ctx is not None else ""
        _epoch = ctx.epoch if ctx is not None else None
        if isinstance(queries, list) and queries:
            items = await _search_multi_async(queries, max_results, sid=_sid, epoch=_epoch)
        else:
            items = await asyncio.wait_for(_search_bing_async(query, max_results), timeout=6.0)
        if ctx is not None and ctx.cancel_check and ctx.cancel_check():
            return {"error": "已取消"}
        if not items:
            return {"results": [], "note": "未检索到结果"}
        lines = []
        for i, it in enumerate(items, 1):
            snippet = it.get("snippet", "")
            lines.append(f"{i}. {it['title']}\n   {it['url']}" + (f"\n   {snippet}" if snippet else ""))
        return {"results": items, "text": "\n".join(lines)}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"web_search async 失败: {e}")
        return {"error": f"检索失败: {str(e)[:200]}"}


async def _web_fetch_async(args: dict, ctx: ToolContext) -> dict:
    if ctx is not None and ctx.cancel_check and ctx.cancel_check():
        return {"error": "已取消"}
    url = (args.get("url") or "").strip()
    max_chars = min(int(args.get("max_chars") or 5000), 20000)
    ok, err = validate_public_url(url)
    if not ok:
        return {"error": err}
    try:
        c = await _async_client()
        r = await asyncio.wait_for(c.get(url), timeout=6.0)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if "application/json" in content_type or url.endswith(".json"):
            body = json.dumps(json_safe(r.json()), ensure_ascii=False, indent=2)
            text = safe_truncate(body, max_chars)
        else:
            text = html_to_text(r.text, max_chars)
        return {"title": _extract_title(r.text), "url": url, "status": r.status_code, "content": text}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"web_fetch async 失败: {url} -> {e}")
        return {"error": f"抓取失败: {str(e)[:200]}"}


async def _http_request_async(args: dict, ctx: ToolContext) -> dict:
    if ctx is not None and ctx.cancel_check and ctx.cancel_check():
        return {"error": "已取消"}
    method = (args.get("method") or "GET").upper()
    url = (args.get("url") or "").strip()
    if method not in _METHODS:
        return {"error": f"method 仅支持 {'/'.join(_METHODS)}"}
    ok, err = validate_public_url(url)
    if not ok:
        return {"error": err}
    headers = args.get("headers") or {}
    if not isinstance(headers, dict):
        return {"error": "headers 必须是 JSON 对象"}
    body = args.get("body")
    try:
        c = await _async_client()
        kwargs = {"headers": {k: str(v) for k, v in headers.items()}}
        if body is not None:
            kwargs["json"] = body
        r = await asyncio.wait_for(c.request(method, url, **kwargs), timeout=10.0)
        text = r.text
        if "application/json" in r.headers.get("content-type", ""):
            try:
                text = json.dumps(json_safe(r.json()), ensure_ascii=False, indent=2)
            except Exception:
                pass
        return {
            "status": r.status_code,
            "url": url,
            "body": safe_truncate(text, 8000),
        }
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"http_request async 失败: {method} {url} -> {e}")
        return {"error": f"请求失败: {str(e)[:200]}"}


def register_http_tools(reg) -> None:
    reg.register(
        ToolSpec(
            name="web_search",
            description="实时联网搜索。当问题需要最新信息（天气、新闻、时事、股价、政策动态等）或知识库外的事实性查询时使用",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，尽量具体（如：珠海今天天气）"},
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                        "description": "多路并行搜索关键词列表（可选，如问题涉及多个方面时：[DeepSeek 公司背景, DeepSeek 技术特点, DeepSeek 开源模型]，会同时搜索并合并结果）",
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
            handler=_web_search_async,
            risk_level=RISK_NORMAL,
            tags=["search", "web"],
        )
    )
    reg.register(
        ToolSpec(
            name="web_fetch",
            description="抓取指定网页的正文内容。用于读取某篇文章、接口文档、新闻详情页等（web_search 只给摘要时用这个拿全文）",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页 URL（仅 http/https）"},
                    "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000, "default": 5000},
                },
                "required": ["url"],
            },
            handler=_web_fetch_async,
            risk_level=RISK_NORMAL,
            tags=["web", "fetch"],
        )
    )
    reg.register(
        ToolSpec(
            name="http_request",
            description="调用任意 REST API（GET/POST/PUT/DELETE/PATCH）。用于对接外部系统接口、查询开放数据源等；写操作需人工确认",
            parameters={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "default": "GET"},
                    "url": {"type": "string", "description": "接口 URL（仅 http/https，禁止内网地址）"},
                    "headers": {"type": "object", "description": '请求头（可选），如 {"Authorization": "Bearer xxx"}'},
                    "body": {"type": "object", "description": "JSON 请求体（可选）"},
                    "timeout": {"type": "integer", "minimum": 3, "maximum": 30, "default": 10},
                },
                "required": ["url"],
            },
            handler=_http_request_async,
            risk_level=RISK_HIGH,  # 对外发起请求（尤其写操作）→ 人工确认
            tags=["http", "api"],
        )
    )
