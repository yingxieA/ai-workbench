"""内置工具：calculator / get_current_time / run_code / memory

- calculator：AST 安全求值（白名单运算符 + 白名单数学函数，绝不 eval）
- get_current_time：当前时间（时区感知）
- run_code：沙箱执行 Python 代码（子进程隔离 + 超时 kill + 输出截断；生产切 Docker 沙箱）
- memory：用户级记忆存取（Redis，按 user_id 隔离命名空间）
"""

from __future__ import annotations

import ast
import datetime as _dt
import math
import operator
import os
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.tools.registry import ToolSpec, ToolContext, RISK_HIGH, RISK_NORMAL
from app.agent.tools.validation import safe_truncate
from app.utils.logger import get_logger

logger = get_logger("tool_builtin_misc")

# ---------- calculator：AST 安全求值 ----------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MATH_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "pi": math.pi,
    "e": math.e,
    "min": min,
    "max": max,
    "pow": pow,
    "factorial": math.factorial,
    "degrees": math.degrees,
    "radians": math.radians,
}


def _ast_eval(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _ast_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("仅支持数值常量")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_ast_eval(node.left), _ast_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_ast_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _MATH_FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError(f"不允许的函数: {node.func.id}")
        return fn(*[_ast_eval(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in _MATH_FUNCS:
        return _MATH_FUNCS[node.id]
    raise ValueError(f"表达式包含不允许的语法: {type(node).__name__}")


def _calculator(args: dict, ctx: ToolContext) -> dict:
    expr = (args.get("expression") or "").strip()
    if not expr:
        return {"error": "expression 不能为空"}
    if len(expr) > 500:
        return {"error": "表达式超长"}
    try:
        tree = ast.parse(expr, mode="eval")
        result = _ast_eval(tree)
        return {"expression": expr, "result": result}
    except Exception as e:
        return {"error": f"计算失败: {str(e)[:200]}"}


# ---------- get_current_time ----------


def _get_current_time(args: dict, ctx: ToolContext) -> dict:
    tz_name = args.get("timezone") or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return {"error": f"未知时区: {tz_name}（如 Asia/Shanghai, UTC）"}
    now = _dt.datetime.now(tz)
    return {
        "timezone": tz_name,
        "datetime": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
    }


# ---------- run_code：沙箱执行（子进程隔离） ----------

_MAX_OUTPUT = 10000
_ALLOWED_BUILTINS = {
    "print",
    "len",
    "range",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "sum",
    "min",
    "max",
    "abs",
    "round",
    "enumerate",
    "zip",
    "sorted",
    "reversed",
    "any",
    "all",
    "map",
    "filter",
    "type",
    "isinstance",
    "format",
    "repr",
    "pow",
    "divmod",
    "hash",
    "chr",
    "ord",
}
# 静态拦截：任何 import/模块/IO/网络相关调用一律禁止（纯计算与数据处理沙箱）
_BLOCKED_FRAGMENTS = (
    "import ",
    "from ",
    "os.",
    "sys.",
    "subprocess",
    "shutil.",
    "socket.",
    "requests",
    "urllib",
    "http",
    "__import__",
    "eval(",
    "exec(",
    "open(",
    "pickle",
    "ctypes",
    "builtins",
    "globals(",
    "locals(",
    "getattr(",
    "setattr(",
)


def _run_code(args: dict, ctx: ToolContext) -> dict:
    code = (args.get("code") or "").strip()
    timeout = min(int(args.get("timeout") or 10), 30)
    if not code:
        return {"error": "code 不能为空"}
    if len(code) > 20000:
        return {"error": "代码超长（>20000 字符）"}

    # 危险语法静态拦截（进程隔离之外的额外防线）
    for bad in _BLOCKED_FRAGMENTS:
        if bad in code:
            return {"error": f"代码包含被禁止的调用: {bad.strip()}（沙箱内不允许）"}

    # 受限内置：wrapper 内用 builtins 模块白名单重定向（type/函数均可，无 JSON 序列化问题）
    _bi_names = ", ".join(repr(k) for k in sorted(_ALLOWED_BUILTINS))
    wrapper = (
        "import builtins as _bi\n"
        "__builtins__ = {{k: getattr(_bi, k) for k in [" + _bi_names + "]}}\n"
        "import sys\n"
        "def _cap(obj, depth=0):\n"
        "    if depth > 3: return str(type(obj).__name__)\n"
        "    if isinstance(obj, (str, int, float, bool, type(None))): return obj\n"
        "    if isinstance(obj, (list, tuple)): return [_cap(x, depth+1) for x in obj[:20]]\n"
        "    if isinstance(obj, dict): return {{str(k): _cap(v, depth+1) for k, v in list(obj.items())[:20]}}\n"
        "    return str(obj)[:200]\n"
        "_out = []\n"
        "def print(*a, **k):\n"
        "    _out.append(' '.join(str(x) for x in a))\n"
        "try:\n"
        "{code}\n"
        "except Exception as e:\n"
        "    print('ERROR:', type(e).__name__, str(e))\n"
        "finally:\n"
        "    print('__OUTPUT_END__')\n"
        "    sys.stdout.write('\\n'.join(_out))\n"
    ).format(code=_indent(code))
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-u", "-c", wrapper],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            cwd=os.path.dirname(os.path.abspath(__file__)) or os.getcwd(),
        )
        out = (proc.stdout or "") + ("\n[stderr] " + proc.stderr if proc.stderr else "")
    except subprocess.TimeoutExpired:
        return {"error": f"执行超时（>{timeout}s，已终止）", "timed_out": True}
    except Exception as e:
        return {"error": f"沙箱启动失败: {str(e)[:200]}"}

    out = out.replace("__OUTPUT_END__", "").strip()
    exit_note = "" if proc.returncode == 0 else f"\n[exit_code={proc.returncode}]"
    return {"stdout": safe_truncate(out, _MAX_OUTPUT) + exit_note}


def _indent(code: str) -> str:
    return "\n".join("    " + line if line.strip() else "" for line in code.splitlines())


# ---------- memory：用户级记忆存取（Redis 隔离命名空间） ----------


def _redis():
    import redis
    from app.config import settings

    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=3)


def _mem_key(ctx: ToolContext, key: str) -> str:
    uid = ctx.user_id or "anonymous"
    return f"agent:mem:{uid}:{key}"


def _memory(args: dict, ctx: ToolContext) -> dict:
    action = args.get("action") or "retrieve"
    key = (args.get("key") or "").strip()
    if not key:
        return {"error": "key 不能为空"}
    if len(key) > 200:
        return {"error": "key 超长"}
    try:
        r = _redis()
    except Exception as e:
        return {"error": f"记忆服务不可用: {str(e)[:100]}"}
    full_key = _mem_key(ctx, key)
    try:
        if action == "store":
            content = args.get("content") or ""
            r.set(full_key, content, ex=60 * 60 * 24 * 30)  # 30 天
            return {"action": "store", "key": key, "stored": True}
        if action == "delete":
            r.delete(full_key)
            return {"action": "delete", "key": key, "deleted": True}
        value = r.get(full_key)
        return {"action": "retrieve", "key": key, "value": value if value is not None else ""}
    except Exception as e:
        return {"error": f"记忆操作失败: {str(e)[:100]}"}


def register_misc_tools(reg) -> None:
    reg.register(
        ToolSpec(
            name="calculator",
            description="数学计算。任何需要精确算术/数学函数求值的问题（税率、换算、百分比、统计量等）都该用它，而不是靠模型心算",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 (1200*0.13)/100，支持 + - * / // % ** 与 sqrt/log/sin 等函数",
                    },
                },
                "required": ["expression"],
            },
            handler=_calculator,
            risk_level=RISK_NORMAL,
            tags=["calc", "math"],
        )
    )
    reg.register(
        ToolSpec(
            name="get_current_time",
            description="获取当前日期时间。任何涉及「今天/现在/几号/星期几/时区」的问题（如查天气要带今天日期）都应先调用它",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "enum": ["Asia/Shanghai", "UTC", "America/New_York", "Europe/London", "Asia/Tokyo"],
                        "default": "Asia/Shanghai",
                    },
                },
            },
            handler=_get_current_time,
            risk_level=RISK_NORMAL,
            tags=["time", "date"],
        )
    )
    reg.register(
        ToolSpec(
            name="run_code",
            description="在隔离沙箱中执行 Python 代码并返回运行结果。用于数据分析、算法验证、文本处理等需要真实运行代码的场景；执行代码需人工确认",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码（仅标准库，禁止网络/系统命令）"},
                    "timeout": {"type": "integer", "minimum": 3, "maximum": 30, "default": 10},
                },
                "required": ["code"],
            },
            handler=_run_code,
            risk_level=RISK_HIGH,  # 执行任意代码 → 人工确认
            tags=["code", "sandbox"],
        )
    )
    reg.register(
        ToolSpec(
            name="memory",
            description="读写用户级长期记忆（按用户隔离）。需要记住用户的偏好、身份、上下文信息时使用",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["retrieve", "store", "delete"], "default": "retrieve"},
                    "key": {"type": "string", "description": "记忆键名（如 user_preference / last_topic）"},
                    "content": {"type": "string", "description": "store 时写入的记忆内容"},
                },
                "required": ["key"],
            },
            handler=_memory,
            risk_level=RISK_NORMAL,
            tags=["memory"],
        )
    )
