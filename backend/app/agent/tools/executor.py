"""工具执行器：校验 → 执行 → 结果治理（高风险工具的 interrupt 由 graph 节点处理）

执行结果统一结构：
    {"success": bool, "tool": str, "result": Any, "error": str|None}
"""

from __future__ import annotations

from app.agent.tools.registry import ToolContext, registry
from app.agent.tools.validation import json_safe, validate_arguments
from app.utils.logger import get_logger

logger = get_logger("tool_executor")


def execute_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    """执行单个工具：参数校验（注入拦截）→ handler → 结果治理"""
    spec = registry.get(name)
    if spec is None:
        return {"success": False, "tool": name, "result": None, "error": f"工具不存在: {name}"}

    ok, err = validate_arguments(spec, args)
    if not ok:
        logger.warning(f"工具 {name} 参数校验拦截: {err}")
        return {"success": False, "tool": name, "result": None, "error": err}

    try:
        raw = spec.handler(args, ctx)
        result = json_safe(raw)
        logger.info(f"工具执行成功: {name} -> {str(result)[:120]}")
        return {"success": True, "tool": name, "result": result, "error": None}
    except Exception as e:
        logger.error(f"工具执行失败: {name} -> {e}")
        return {"success": False, "tool": name, "result": None, "error": f"执行异常: {str(e)[:200]}"}


def execute_tools_parallel(names: list[str], args_map: dict[str, dict], ctx: ToolContext) -> list[dict]:
    """并行执行多个工具（每工具独立线程，互不阻塞）"""
    import concurrent.futures

    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(names), 5)) as pool:
        futures = {pool.submit(execute_tool, n, args_map.get(n, {}), ctx): n for n in names}
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
    return results
