"""内置工具：web_search / web_fetch / http_request

- web_search：实时搜索（DuckDuckGo HTML 端点，无需 key；Bing 兜底）
  生产提示：可无缝替换为 Apify / SerpAPI 等商业检索源（settings.APIFY_API_KEY 已预留）
- web_fetch：抓取网页正文（防 SSRF + 输出截断）
- http_request：通用 REST 调用（防 SSRF + 方法白名单 + 高风险需确认）
"""

from __future__ import annotations

import json
import re

import httpx

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
_TIMEOUT = httpx.Timeout(12.0)


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True)


# ---------- web_search ----------


def _search_ddg(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo HTML 端点检索"""
    with _client() as c:
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
    with _client() as c:
        r = c.get("https://www.bing.com/search", params={"q": query})
        r.raise_for_status()
    items = []
    for m in re.finditer(r'<li class="b_algo".*?<h2><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.DOTALL):
        title = re.sub(r"<[^>]+>", "", m.group(2))
        item = {"title": safe_truncate(title, 200), "url": m.group(1)}
        snippet_m = re.search(r"<p[^>]*>(.*?)</p>", m.group(0), re.DOTALL)
        if snippet_m:
            item["snippet"] = safe_truncate(re.sub(r"<[^>]+>", "", snippet_m.group(1)), 400)
        items.append(item)
        if len(items) >= max_results:
            break
    return items


def _web_search(args: dict, ctx: ToolContext) -> dict:
    query = (args.get("query") or "").strip()
    max_results = min(int(args.get("max_results") or 5), 10)
    if not query:
        return {"error": "query 不能为空"}
    try:
        try:
            items = _search_ddg(query, max_results)
        except Exception as e:
            logger.warning(f"DDG 检索失败，切 Bing: {e}")
            items = _search_bing(query, max_results)
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
    url = (args.get("url") or "").strip()
    max_chars = min(int(args.get("max_chars") or 5000), 20000)
    ok, err = validate_public_url(url)
    if not ok:
        return {"error": err}
    try:
        with _client() as c:
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
        with _client() as c:
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


def register_http_tools(reg) -> None:
    reg.register(
        ToolSpec(
            name="web_search",
            description="实时联网搜索。当问题需要最新信息（天气、新闻、时事、股价、政策动态等）或知识库外的事实性查询时使用",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，尽量具体（如：珠海今天天气）"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
            handler=_web_search,
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
            handler=_web_fetch,
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
            handler=_http_request,
            risk_level=RISK_HIGH,  # 对外发起请求（尤其写操作）→ 人工确认
            tags=["http", "api"],
        )
    )
