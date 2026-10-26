"""工具执行器：校验 → 执行 → 结果治理（高风险工具的 interrupt 由 graph 节点处理）

执行结果统一结构：
    {"success": bool, "tool": str, "result": Any, "error": str|None}
"""

from __future__ import annotations

import asyncio
import inspect

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
        if inspect.isawaitable(raw):
            raw = asyncio.run(raw)  # 同步上下文调用 async handler：临时 loop 跑完（脚本/单测用）
        result = json_safe(raw)
        logger.info(f"工具执行成功: {name} -> {str(result)[:120]}")
        return {"success": True, "tool": name, "result": result, "error": None}
    except Exception as e:
        logger.error(f"工具执行失败: {name} -> {e}")
        return {"success": False, "tool": name, "result": None, "error": f"执行异常: {str(e)[:200]}"}


async def aexecute_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    """async 工具执行器（方案 B 真取消）：
    - async handler → 直接 await（协程级可被 task.cancel 强杀，httpx 请求即时终止）
    - 同步 handler → asyncio.to_thread（不阻塞事件循环；同步函数内部靠 P2-A 取消检查压窗口）
    取消（CancelledError）直接冒泡给调用方，由节点/图收尾。
    """
    spec = registry.get(name)
    if spec is None:
        return {"success": False, "tool": name, "result": None, "error": f"工具不存在: {name}"}
    ok, err = validate_arguments(spec, args)
    if not ok:
        logger.warning(f"工具 {name} 参数校验拦截: {err}")
        return {"success": False, "tool": name, "result": None, "error": err}
    try:
        if inspect.iscoroutinefunction(spec.handler):
            raw = await spec.handler(args, ctx)
        else:
            raw = await asyncio.to_thread(spec.handler, args, ctx)
        result = json_safe(raw)
        logger.info(f"工具 async 执行成功: {name} -> {str(result)[:120]}")
        return {"success": True, "tool": name, "result": result, "error": None}
    except asyncio.CancelledError:
        raise  # 方案 B：取消直接冒泡（task.cancel 强杀）
    except Exception as e:
        logger.error(f"工具 async 执行失败: {name} -> {e}")
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
