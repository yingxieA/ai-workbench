"""自实现标准 MCP Server（JSON-RPC 2.0 over SSE，2025-03-26 协议）

- GET  /mcp   建立 SSE 会话（event: endpoint → event: message 推送）
- POST /mcp   客户端 JSON-RPC 请求（initialize / tools/list / tools/call / ping）
- 工具声明直接来自注册池 ToolSpec（name/description/inputSchema 精确透传）

实现动机：mcp 2.x 的 MCPServer.tool() 不支持显式参数 Schema（仅函数签名推导），
而我们的工具池是声明式 JSON Schema，自实现可精确透传且不依赖 SDK 版本。
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.agent.tools.registry import registry
from app.agent.tools.executor import execute_tool
from app.agent.tools.registry import ToolContext
from app.utils.logger import get_logger

logger = get_logger("mcp_server")

router = APIRouter(prefix="/mcp", tags=["mcp"])

PROTOCOL_VERSION = "2025-03-26"
_sessions: dict[str, asyncio.Queue] = {}


def _result(**kwargs) -> dict:
    return {"jsonrpc": "2.0", **kwargs}


async def _handle_request(body: dict, session_id: str | None) -> dict:
    """处理一条 JSON-RPC 请求，返回响应（notification 返回 None）"""
    method = body.get("method", "")
    msg_id = body.get("id")

    if method == "initialize":
        return _result(
            id=msg_id,
            result={
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ai-workbench-tools", "version": "0.1.0"},
            },
        )

    if method == "notifications/initialized":
        return None  # 通知无需响应

    if method == "ping":
        return _result(id=msg_id, result={})

    if method == "tools/list":
        tools = [
            {
                "name": s.name,
                "description": s.description,
                "inputSchema": s.parameters,
            }
            for s in registry.all()
        ]
        return _result(id=msg_id, result={"tools": tools})

    if method == "tools/call":
        params = body.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments") or {}
        spec = registry.get(name)
        if spec is None:
            return _result(
                id=msg_id,
                error={
                    "code": -32602,
                    "message": f"tool not found: {name}",
                },
            )
        r = execute_tool(name, args, ToolContext(user_id=None))
        if r["success"]:
            text = json.dumps(r.get("result"), ensure_ascii=False, default=str)
            return _result(
                id=msg_id,
                result={
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            )
        return _result(
            id=msg_id,
            result={
                "content": [{"type": "text", "text": r.get("error") or "unknown error"}],
                "isError": True,
            },
        )

    return _result(id=msg_id, error={"code": -32601, "message": f"method not found: {method}"})


@router.get("")
async def mcp_sse(request: Request):
    """建立 SSE 会话：先发 endpoint 事件，再持续推送 message 事件"""
    session_id = uuid.uuid4().hex
    q: asyncio.Queue = asyncio.Queue()
    _sessions[session_id] = q
    logger.info(f"MCP SSE 会话建立: {session_id}")

    async def gen():
        try:
            yield f"event: endpoint\ndata: {json.dumps({'url': f'/mcp?session_id={session_id}'})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # SSE 注释心跳
                    continue
                yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.warning(f"MCP SSE 会话结束 {session_id}: {e}")
        finally:
            _sessions.pop(session_id, None)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("")
async def mcp_post(request: Request, session_id: str | None = None):
    """客户端 JSON-RPC 请求（session_id 走 query 参数）"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}, "id": None})

    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32600, "message": "invalid request"}, "id": None})

    resp = await _handle_request(body, session_id)
    if resp is None:
        return JSONResponse({}, status_code=200)  # 通知

    # 有响应且请求带 id → 若有活跃 SSE 会话则经会话队列推送，否则直接返回
    if body.get("id") is not None and session_id and session_id in _sessions:
        try:
            await _sessions[session_id].put(resp)
            return JSONResponse({}, status_code=202)  # SSE 模式：响应走消息通道
        except Exception:
            pass
    return JSONResponse(resp)
