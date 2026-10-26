# -*- coding: utf-8 -*-
"""系统配置管理 API（仅 admin）：热更新，无需重启

能力：
- GET  /api/system-configs        —— 全部配置列表（含描述/操作人/更新时间）
- GET  /api/system-configs/meta   —— 配置元数据（分组 + 类型，供管理界面渲染与校验）
- PUT  /api/system-configs        —— 更新（按类型校验：int/float/bool/json/text，非法值拒绝写入）
- GET  /api/system-configs/history—— 单键变更历史（审计日志回溯）

生效链路：管理界面改配置 → 写 DB → 删 Redis 缓存 → 下个请求回源生效（无需重启）
"""

from app.core.errors import ErrorCode
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import require_admin
from app.models.user import User
from app.models.document import AuditLog
from app.services.system_configs import list_configs, set_config
from app.utils.logger import get_logger

logger = get_logger("system_configs")
router = APIRouter(prefix="/api/system-configs", tags=["system-configs"])

# ---------------------------------------------------------------------------
# 配置元数据：key → {group 分组, type 值类型}
# 管理界面渲染（分组 Tag / 类型 Tag / 编辑控件）与后端校验共用此单一权威源；
# 未登记的动态键按 {group: "其他", type: "text"} 兜底。
# ---------------------------------------------------------------------------
CONFIG_META: dict[str, dict] = {
    # 模型链 / 生成
    "llm_model_chain": {"group": "模型链", "type": "json"},
    "llm_temperature": {"group": "模型链", "type": "float"},
    "model_pricing": {"group": "模型链", "type": "json"},
    # Prompt 模板
    "prompt_super_router": {"group": "Prompt", "type": "text"},
    "prompt_rag_system": {"group": "Prompt", "type": "text"},
    "prompt_direct_system": {"group": "Prompt", "type": "text"},
    "prompt_tool_system": {"group": "Prompt", "type": "text"},
    # Agent Loop 循环控制
    "agent_loop_max_steps": {"group": "Loop 循环", "type": "int"},
    "agent_loop_step_llm_timeout": {"group": "Loop 循环", "type": "int"},
    "agent_loop_tool_timeout": {"group": "Loop 循环", "type": "int"},
    "agent_loop_tool_retry": {"group": "Loop 循环", "type": "int"},
    "agent_loop_total_timeout": {"group": "Loop 循环", "type": "int"},
    # 意图路由
    "router_confidence_threshold": {"group": "意图路由", "type": "float"},
    "router_intent_patterns": {"group": "意图路由", "type": "json"},
    # 检索 / 分块
    "rag_top_k_candidate": {"group": "检索分块", "type": "int"},
    "rag_top_k_final": {"group": "检索分块", "type": "int"},
    "rag_chunk_size": {"group": "检索分块", "type": "int"},
    "rag_chunk_overlap": {"group": "检索分块", "type": "int"},
    # 安全护栏
    "guardrail_inject_high": {"group": "安全护栏", "type": "json"},
    "guardrail_inject_probe": {"group": "安全护栏", "type": "json"},
    "guardrail_indirect_markers": {"group": "安全护栏", "type": "json"},
    "guardrail_output_markers": {"group": "安全护栏", "type": "json"},
    "guardrail_injection_enabled": {"group": "安全护栏", "type": "bool"},
    "guardrail_output_enabled": {"group": "安全护栏", "type": "bool"},
    "guardrail_output_llm_enabled": {"group": "安全护栏", "type": "bool"},
    "prompt_output_llm_audit": {"group": "安全护栏", "type": "text"},
    # 多模态 RAG（图片图注索引 + 图文回答）
    "vision_enabled": {"group": "多模态RAG", "type": "bool"},
    "vision_model": {"group": "多模态RAG", "type": "text"},
    "vision_prompt": {"group": "多模态RAG", "type": "text"},
    "vision_generate_model": {"group": "多模态RAG", "type": "text"},
    # 前置过滤
    "prefilter_light_enabled": {"group": "前置过滤", "type": "bool"},
    "prefilter_light_reply": {"group": "前置过滤", "type": "text"},
    # 记忆 / 多轮对话
    "chat_redis_cache_enabled": {"group": "记忆对话", "type": "bool"},
    "chat_summary_enabled": {"group": "记忆对话", "type": "bool"},
    "chat_window_rounds": {"group": "记忆对话", "type": "int"},
    # 成本监控
    "cost_guard_enabled": {"group": "成本监控", "type": "bool"},
    "daily_cost_limit": {"group": "成本监控", "type": "float"},
    # 测评中心
    "eval_enabled": {"group": "测评中心", "type": "bool"},
    "eval_cron": {"group": "测评中心", "type": "text"},
    "eval_mode": {"group": "测评中心", "type": "text"},
    "eval_limit": {"group": "测评中心", "type": "int"},
    # 检索增强
    "query_rewrite_enabled": {"group": "检索增强", "type": "bool"},
}

_GROUP_ORDER = [
    "模型链",
    "Prompt",
    "Loop 循环",
    "意图路由",
    "检索分块",
    "安全护栏",
    "前置过滤",
    "记忆对话",
    "成本监控",
    "测评中心",
    "检索增强",
    "其他",
]


def _meta_of(key: str) -> dict:
    return CONFIG_META.get(key, {"group": "其他", "type": "text"})


def _validate_and_normalize(key: str, value: str) -> str:
    """按类型校验并规范化值；非法返回带原因的 (False, msg)，合法返回 (True, 规范化后值)"""
    typ = _meta_of(key)["type"]
    raw = value.strip()
    if not raw:
        return False, "value 不能为空"
    try:
        if typ == "int":
            n = int(raw)
            if n < 0:
                return False, "int 类型不能为负数"
            return True, str(n)
        if typ == "float":
            f = float(raw)
            if f < 0:
                return False, "float 类型不能为负数"
            return True, str(f)
        if typ == "bool":
            low = raw.lower()
            if low in ("true", "1", "yes", "on"):
                return True, "true"
            if low in ("false", "0", "no", "off"):
                return True, "false"
            return False, "bool 类型仅接受 true/false/1/0/yes/no/on/off"
        if typ == "json":
            obj = json.loads(raw)
            # 规范化：数组/对象原样，其他包装为对象，避免裸标量
            if isinstance(obj, (dict, list)):
                return True, json.dumps(obj, ensure_ascii=False)
            return False, "json 类型要求 JSON 数组或对象（如 [...] 或 {...}）"
        return True, value  # text 不校验
    except json.JSONDecodeError as e:
        return False, f"JSON 解析失败：{e}"
    except ValueError:
        return False, f"无法解析为 {typ} 类型"


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str
    description: str = ""


def _audit(db: Session, user: User, action: str, detail: dict):
    try:
        db.add(AuditLog(user_id=user.id, action=action, detail=detail))
    except Exception as e:
        logger.warning(f"写审计日志失败: {e}")


@router.get("")
def get_all_configs(admin: User = Depends(require_admin)):
    items = list_configs()
    return {"items": items, "total": len(items)}


@router.get("/meta")
def get_config_meta(admin: User = Depends(require_admin)):
    """分组 + 类型元数据；动态键兜底为 其他/text，分组顺序固定"""
    items = list_configs()
    meta = {}
    for it in items:
        key = it["key"]
        m = _meta_of(key)
        meta[key] = {"group": m["group"], "type": m["type"]}
    return {"meta": meta, "groups": _GROUP_ORDER}


@router.get("/history")
def get_config_history(
    key: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """单键变更历史：action=update_config 且 detail->>'key' 匹配"""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action == "update_config")
        .filter(AuditLog.detail["key"].astext == key)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "time": str(r.created_at),
                "operator": r.user_id,
                "value": (r.detail or {}).get("value", ""),
                "action": (r.detail or {}).get("action", "update"),
            }
            for r in rows
        ]
    }


@router.put("")
def update_config(req: ConfigUpdateRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """更新配置：按类型校验 + 规范化，通过后热更新立即生效"""
    req.key = (req.key or "").strip()
    if not req.key:
        raise HTTPException(status_code=400, detail=(ErrorCode.CONFIG_KEY_EMPTY, "key 不能为空"))

    ok, msg = _validate_and_normalize(req.key, req.value)
    if not ok:
        raise HTTPException(status_code=400, detail=f"值校验失败（{msg}）")

    result = set_config(req.key, msg if ok else req.value, req.description, admin.username)
    _audit(db, admin, "update_config", {"key": req.key, "value": result["value"], "action": result["action"]})
    db.commit()
    return result
