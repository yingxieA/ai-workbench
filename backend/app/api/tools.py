"""工具管理 API（P2.2）：工具池可视化 / 启停 / 外部 MCP 注册与卸载

- GET  /api/tools                工具池列表（含状态、来源、风险）
- PUT  /api/tools/{name}/toggle  启停工具（热切换，无需重启）
- POST /api/tools/external       注册外部 MCP Server（stdio 命令 / SSE URL / Streamable HTTP URL），工具注入池并持久化
- DELETE /api/tools/external/{name}  卸载外部工具（池 + DB）

仅 admin（role_level >= 100）可操作；启停状态持久化到 agent_tools 表，重启自动恢复。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agent.tools.registry import registry, RISK_HIGH, ToolSpec
from app.api.auth import require_admin
from app.database import SessionLocal
from app.models.document import AgentToolConfig
from app.utils.logger import get_logger

logger = get_logger("api_tools")
router = APIRouter(prefix="/api/tools", tags=["tools"])

_RISK_TEXT = {RISK_HIGH: "需人工确认", "normal": "自动执行"}


class ExternalMcpRequest(BaseModel):
    name: str  # 连接名称（入库标识）
    conn_type: str = "stdio"  # stdio | sse | streamable_http
    command: str = ""  # stdio：外部 MCP 启动命令（如 npx @xxx/server）
    args: list[str] = []  # stdio：命令参数
    url: str = ""  # sse：外部 MCP Server 端点
    prefix: str = "ext_"
    # streamable_http 场景：key 可直接拼在 url query 上（如高德 https://mcp.amap.com/mcp?key=xxx）


def _spec_to_dict(spec: ToolSpec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "risk_level": spec.risk_level,
        "risk_text": _RISK_TEXT.get(spec.risk_level, spec.risk_level),
        "source": spec.source,
        "tags": spec.tags,
        "enabled": spec.enabled,
        "parameters": spec.parameters,
        "connect_config": spec.connect_config,
    }


@router.get("")
def list_tools(_user=Depends(require_admin)):
    """工具池列表（含已停用，管理界面展示用）"""
    specs = registry.list_all()
    return {"tools": [_spec_to_dict(s) for s in specs], "count": len(specs)}


@router.put("/{name}/toggle")
def toggle_tool(name: str, _user=Depends(require_admin)):
    """启停工具：切换 registry 状态 + 持久化到 DB（重启后恢复）"""
    spec = registry.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"工具不存在: {name}")
    new_state = not spec.enabled
    if not registry.set_enabled(name, new_state):
        raise HTTPException(status_code=500, detail="切换失败")
    # 持久化
    db = SessionLocal()
    try:
        row = db.query(AgentToolConfig).filter(AgentToolConfig.name == name).first()
        if row:
            row.enabled = new_state
        else:
            db.add(
                AgentToolConfig(
                    name=name,
                    description=spec.description,
                    risk_level=spec.risk_level,
                    source=spec.source,
                    connect_config=spec.connect_config,
                    enabled=new_state,
                )
            )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"持久化工具状态失败: {e}")
    finally:
        db.close()
    return {"ok": True, "name": name, "enabled": new_state}


@router.post("/external")
def register_external(req: ExternalMcpRequest, _user=Depends(require_admin)):
    """注册外部 MCP Server：连接拉取工具 → 注入工具池 → 持久化（重启自动重连）"""
    if req.conn_type not in ("stdio", "sse", "streamable_http"):
        raise HTTPException(status_code=400, detail="conn_type 仅支持 stdio / sse / streamable_http")
    if req.conn_type == "stdio" and not req.command.strip():
        raise HTTPException(status_code=400, detail="stdio 模式必须提供 command")
    if req.conn_type in ("sse", "streamable_http") and not req.url.strip():
        raise HTTPException(status_code=400, detail=f"{req.conn_type} 模式必须提供 url")

    from app.agent.tools.mcp import connect_mcp_stdio, connect_mcp_sse, connect_mcp_streamable_http

    try:
        if req.conn_type == "stdio":
            registered = connect_mcp_stdio(req.command.strip(), req.args or [], prefix=req.prefix or "ext_")
            connect_config = {"type": "stdio", "command": req.command.strip(), "args": req.args or []}
        elif req.conn_type == "sse":
            registered = connect_mcp_sse(req.url.strip(), prefix=req.prefix or "ext_")
            connect_config = {"type": "sse", "url": req.url.strip()}
        else:
            registered = connect_mcp_streamable_http(req.url.strip(), prefix=req.prefix or "ext_")
            connect_config = {"type": "streamable_http", "url": req.url.strip()}
    except Exception as e:
        logger.error(f"外部 MCP 连接失败: {e}")
        raise HTTPException(status_code=502, detail=f"连接外部 MCP 失败: {str(e)[:200]}")

    if not registered:
        raise HTTPException(status_code=422, detail="连接成功但未拉取到任何工具（检查 Server 是否实现了 tools/list）")

    # 持久化连接配置（含来源标记）
    db = SessionLocal()
    try:
        for tool_name in registered:
            spec = registry.get(tool_name)
            if spec is None:
                continue
            spec.source = "external"
            spec.connect_config = connect_config
            row = db.query(AgentToolConfig).filter(AgentToolConfig.name == tool_name).first()
            if row:
                row.enabled = True
                row.source = "external"
                row.connect_config = connect_config
            else:
                db.add(
                    AgentToolConfig(
                        name=tool_name,
                        description=spec.description,
                        risk_level=spec.risk_level,
                        source="external",
                        connect_config=connect_config,
                        enabled=True,
                    )
                )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"持久化外部工具失败: {e}")
    finally:
        db.close()

    return {"ok": True, "registered": registered, "connect_config": connect_config}


@router.delete("/external/{name}")
def unregister_external(name: str, _user=Depends(require_admin)):
    """卸载外部工具：从工具池移除 + 删除 DB 记录"""
    spec = registry.get(name)
    if spec is None or spec.source != "external":
        raise HTTPException(status_code=404, detail=f"外部工具不存在: {name}")
    registry.unregister(name)
    db = SessionLocal()
    try:
        row = db.query(AgentToolConfig).filter(AgentToolConfig.name == name).first()
        if row:
            db.delete(row)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"删除外部工具记录失败: {e}")
    finally:
        db.close()
    return {"ok": True, "name": name}


def sync_tools_from_db() -> None:
    """启动时调用：把 DB 中保存的工具启停状态同步到内存池，并自动重连启用的外部 MCP"""
    db = SessionLocal()
    rows = []
    try:
        rows = db.query(AgentToolConfig).all()
    except Exception as e:
        logger.warning(f"读取工具配置失败（首次启动无表？）: {e}")
        return
    finally:
        db.close()
    seen_configs: set[tuple] = set()  # 同一连接配置只重连一次（DB 每工具一行，避免 N 次冗余连接）
    for row in rows:
        if row.source == "external" and row.enabled and row.connect_config:
            try:
                cfg = row.connect_config
                cfg_key = (cfg.get("type"), cfg.get("url"), cfg.get("command"))
                if cfg_key in seen_configs:
                    registry.set_enabled(row.name, True)  # 该连接已重连注册，直接恢复启用
                    continue
                seen_configs.add(cfg_key)
                # prefix 从 DB 行名推导（如 amap_maps_weather -> amap_），
                # 保证重连注册的工具名与持久化行名一致，避免重复注册
                prefix = row.name.split("_", 1)[0] + "_"
                if cfg.get("type") == "stdio":
                    from app.agent.tools.mcp import connect_mcp_stdio

                    registered = connect_mcp_stdio(cfg.get("command", ""), cfg.get("args") or [], prefix=prefix)
                    logger.info(f"自动重连外部 MCP(stdio): {row.name} -> {registered}")
                    for n in registered:
                        sp = registry.get(n)
                        if sp:
                            sp.source = "external"
                            sp.connect_config = cfg
                elif cfg.get("type") == "sse":
                    from app.agent.tools.mcp import connect_mcp_sse

                    registered = connect_mcp_sse(cfg.get("url", ""), prefix=prefix)
                    logger.info(f"自动重连外部 MCP(sse): {row.name} -> {registered}")
                    for n in registered:
                        sp = registry.get(n)
                        if sp:
                            sp.source = "external"
                            sp.connect_config = cfg
                elif cfg.get("type") == "streamable_http":
                    from app.agent.tools.mcp import connect_mcp_streamable_http

                    registered = connect_mcp_streamable_http(cfg.get("url", ""), prefix=prefix)
                    logger.info(f"自动重连外部 MCP(streamable_http): {row.name} -> {registered}")
                    for n in registered:
                        sp = registry.get(n)
                        if sp:
                            sp.source = "external"
                            sp.connect_config = cfg
                else:
                    registry.set_enabled(row.name, row.enabled)
            except Exception as e:
                logger.error(f"自动重连外部 MCP 失败 {row.name}: {e}")
                registry.set_enabled(row.name, False)  # 重连失败默认停用，避免路由选中不可用工具
        else:
            registry.set_enabled(row.name, row.enabled)
    logger.info("工具池状态与 DB 同步完成")
