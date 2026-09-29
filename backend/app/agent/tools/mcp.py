"""MCP 接入：双向

1. MCP Server（对外暴露）：自实现标准 MCP 协议（JSON-RPC over SSE），
   见 mcp_server.py，挂载 FastAPI /mcp 端点——外部系统可通过 MCP 协议调用本工作台工具池。
2. MCP Client（对内拉取）：连接外部 MCP Server（stdio 命令 / SSE URL / Streamable HTTP URL），
   拉取其工具列表并包装成 ToolSpec 注入全局工具池 → 外部 MCP 工具直接进池可被 Router 选用。

注意：外部工具执行时「每次调用动态建连」（_session_ctx），
不依赖注册时的 session 闭包——注册后连接即关闭，闭包调用会 Connection closed。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent.tools.registry import ToolContext, ToolSpec, registry, RISK_HIGH
from app.utils.logger import get_logger

logger = get_logger("tool_mcp")


def connect_mcp_stdio(command: str, args: list[str] | None = None, prefix: str = "ext_") -> list[str]:
    """连接 stdio 型外部 MCP Server，把其工具包装注册进工具池。返回注册的工具名列表"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command, args=args or [], env=None)
    connect_config = {"type": "stdio", "command": command, "args": args or []}

    def _run() -> list[str]:
        async def _inner() -> list[str]:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    registered: list[str] = []
                    for t in tools.tools:
                        spec = _wrap_external_tool(t, prefix, connect_config)
                        try:
                            registry.register(spec)
                            registered.append(spec.name)
                        except ValueError as e:
                            logger.warning(f"外部工具注册跳过: {e}")
                    return registered

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_inner())
        finally:
            loop.close()

    return _run()


def connect_mcp_sse(url: str, prefix: str = "ext_") -> list[str]:
    """连接 SSE 型外部 MCP Server（如远程 MCP 网关）"""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    connect_config = {"type": "sse", "url": url}

    def _run() -> list[str]:
        async def _inner() -> list[str]:
            async with sse_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    registered: list[str] = []
                    for t in tools.tools:
                        spec = _wrap_external_tool(t, prefix, connect_config)
                        try:
                            registry.register(spec)
                            registered.append(spec.name)
                        except ValueError as e:
                            logger.warning(f"外部工具注册跳过: {e}")
                    return registered

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_inner())
        finally:
            loop.close()

    return _run()


def connect_mcp_streamable_http(url: str, prefix: str = "ext_") -> list[str]:
    """连接 Streamable HTTP 型外部 MCP Server（如高德官方 MCP，2026 起推荐）"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    connect_config = {"type": "streamable_http", "url": url}

    def _run() -> list[str]:
        async def _inner() -> list[str]:
            async with streamable_http_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    registered: list[str] = []
                    for t in tools.tools:
                        spec = _wrap_external_tool(t, prefix, connect_config)
                        try:
                            registry.register(spec)
                            registered.append(spec.name)
                        except ValueError as e:
                            logger.warning(f"外部工具注册跳过: {e}")
                    return registered

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_inner())
        finally:
            loop.close()

    return _run()


class _SessionCtx:
    """按连接配置动态建立 MCP ClientSession（每次调用重建，避免闭包 session 失效）"""

    def __init__(self, connect_config: dict):
        self._connect_config = connect_config
        self._cm = None
        self.session = None

    async def __aenter__(self):
        from mcp import ClientSession

        ctype = self._connect_config.get("type", "streamable_http")
        if ctype == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self._connect_config.get("command", ""),
                args=self._connect_config.get("args") or [],
                env=None,
            )
            self._cm = stdio_client(params)
        elif ctype == "sse":
            from mcp.client.sse import sse_client

            self._cm = sse_client(self._connect_config.get("url", ""))
        else:
            from mcp.client.streamable_http import streamable_http_client

            self._cm = streamable_http_client(self._connect_config.get("url", ""))
        read, write = await self._cm.__aenter__()
        self.session = ClientSession(read, write)
        await self.session.__aenter__()
        await self.session.initialize()
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session is not None:
            await self.session.__aexit__(exc_type, exc_val, exc_tb)
        if self._cm is not None:
            await self._cm.__aexit__(exc_type, exc_val, exc_tb)


def _wrap_external_tool(tool, prefix: str, connect_config: dict) -> ToolSpec:
    """把 MCP 工具声明包装成本地 ToolSpec（调用时按 connect_config 动态建连，转发 call_tool）"""
    name = f"{prefix}{tool.name}"
    parameters = dict(tool.input_schema or {"type": "object", "properties": {}, "required": []})
    # 外部工具默认按普通风险执行；描述含敏感词（删除/写/执行）的升级为 high
    risk = (
        RISK_HIGH
        if any(
            w in tool.description.lower() for w in ("delete", "remove", "write", "execute", "send", "pay", "transfer")
        )
        else "normal"
    )

    def handler(args: dict, ctx: ToolContext) -> Any:
        async def _call() -> Any:
            async with _SessionCtx(connect_config) as session:
                result = await session.call_tool(tool.name, arguments=args)
            parts = [getattr(c, "text", None) for c in result.content]
            text = "\n".join(p for p in parts if p)
            is_error = getattr(result, "isError", False)
            if is_error:
                raise RuntimeError(text or "外部 MCP 工具执行失败")
            return {"mcp_tool": tool.name, "result": text}

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_call())
        finally:
            loop.close()

    return ToolSpec(
        name=name,
        description=f"[MCP:{tool.name}] {tool.description}",
        parameters=parameters,
        handler=handler,
        risk_level=risk,
        tags=["mcp", "external"],
    )
