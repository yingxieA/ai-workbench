"""工具参数与安全校验

- validate_arguments：jsonschema 校验（必填 / 类型 / enum / pattern）→ 拦截参数注入
- validate_public_url：防 SSRF（仅 http/https、禁内网/环回/链路本地地址）
- safe_truncate / decode_html：输出治理（截断、实体解码）
"""

from __future__ import annotations

import html as _html
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from app.utils.logger import get_logger

logger = get_logger("tool_validation")

MAX_PARAM_LENGTH = 20000  # 单参数字符上限
MAX_RESULT_LENGTH = 12000  # 工具结果文本上限

_INTERNAL_NET_RE = re.compile(r"^(127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.|0\.)")


def validate_arguments(spec, args: dict) -> tuple[bool, str]:
    """JSON Schema 校验工具参数。返回 (ok, error_msg)"""
    if not isinstance(args, dict):
        return False, "参数必须是 JSON 对象"
    # 长度治理：防超长参数注入
    for k, v in args.items():
        if isinstance(v, str) and len(v) > MAX_PARAM_LENGTH:
            return False, f"参数 {k} 超长（>{MAX_PARAM_LENGTH} 字符）"
        if isinstance(v, (dict, list)):
            try:
                import json as _json

                if len(_json.dumps(v, ensure_ascii=False)) > MAX_PARAM_LENGTH:
                    return False, f"参数 {k} 序列化后超长"
            except Exception:
                pass
    try:
        validator = Draft202012Validator(
            spec.parameters,
            format_checker=FormatChecker(),
        )
        validator.validate(args)
        return True, ""
    except ValidationError as e:
        path = ".".join(str(p) for p in e.path) or "(root)"
        return False, f"参数校验失败 [{path}]: {e.message}"


def resolve_host(host: str) -> str | None:
    """解析主机名到 IP（返回 IP 字符串；解析失败返回 None）"""
    try:
        return socket.gethostbyname(host.strip())
    except OSError:
        return None


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        # 解析失败或非标准 IP → 保守拦截
        return True


def validate_public_url(url: str, allow_domains: list[str] | None = None) -> tuple[bool, str]:
    """防 SSRF：仅 http/https；域名白名单（可选）；禁止内网/环回/链路本地地址"""
    if not url or len(url) > 2048:
        return False, "URL 为空或超长"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"仅允许 http/https 协议，收到: {parsed.scheme or '无'}"
    host = parsed.hostname or ""
    if not host:
        return False, "URL 缺少主机名"
    # 域名白名单（配置了才启用；默认不限制域名，但始终拦截内网地址）
    if allow_domains:
        if not any(host == d or host.endswith("." + d) for d in allow_domains):
            return False, f"域名不在白名单内: {host}"
    # 字面 IP 直接判断；域名则解析后判断
    if re.match(r"^[\d.]+$", host) or ":" in host:
        if _is_private_ip(host):
            return False, f"禁止访问内网/环回地址: {host}"
    else:
        ip = resolve_host(host)
        if ip is None:
            return False, f"域名解析失败: {host}"
        if _is_private_ip(ip):
            return False, f"禁止访问内网地址: {host} ({ip})"
    return True, ""


def safe_truncate(text: Any, max_len: int = MAX_RESULT_LENGTH) -> str:
    """结果治理：非字符串先转 str，再截断 + 去除不可见字符"""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            text = repr(text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if len(text) > max_len:
        text = text[:max_len] + f"\n…(已截断，共 {len(text)} 字符)"
    return text


def html_to_text(raw: str, max_len: int = MAX_RESULT_LENGTH) -> str:
    """极简 HTML → 纯文本（去除脚本/样式/标签，解码实体）"""
    text = raw or ""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|h[1-6]|li|tr)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return safe_truncate(text.strip(), max_len)


def json_safe(value: Any, max_len: int = MAX_RESULT_LENGTH) -> Any:
    """把不可序列化对象转成可 JSON 序列化值"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value][:50]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in list(value.items())[:50]}
    return safe_truncate(str(value), max_len)
