"""工具注册机制：声明式 ToolSpec + 全局注册池（一切皆插件）

- ToolSpec：名称 / 描述 / 参数 JSON Schema / 风险等级 / handler
- registry.register(spec)：注入工具池，Router 据此选择工具，tool_execute 据此执行
- 新增工具不改核心代码：实现一个 ToolSpec 并 register 即可
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.utils.logger import get_logger

logger = get_logger("tool_registry")

# 风险等级：normal=自动执行；high=需人工确认后才能执行
RISK_NORMAL = "normal"
RISK_HIGH = "high"


class ToolContext:
    """工具执行上下文：调用方注入，handler 可按需使用"""

    def __init__(self, user_id: str | None = None, session_id: str | None = None, question: str = ""):
        self.user_id = user_id
        self.session_id = session_id
        self.question = question


@dataclass
class ToolSpec:
    name: str  # 工具名（snake_case，注册池内唯一）
    description: str  # 给 LLM 看的用途说明（何时该调用）
    parameters: dict  # JSON Schema（type=object + properties + required）
    handler: Callable[[dict, ToolContext], Any]  # (args, ctx) -> 结果（任意可 JSON 序列化值）
    risk_level: str = RISK_NORMAL  # normal / high
    tags: list = field(default_factory=list)  # 分类标签（search/http/code/memory/time/calc）
    enabled: bool = True
    source: str = "builtin"  # builtin / external（外部 MCP 拉取）
    connect_config: Optional[dict] = None  # 外部连接配置（DB 持久化用）

    def summarize(self) -> str:
        """给 Router 的工具清单行（一行一个工具）"""
        risk = "需确认" if self.risk_level == RISK_HIGH else ""
        return f"- {self.name}：{self.description}（参数：{_params_brief(self.parameters)}）{risk}"


def _params_brief(parameters: dict) -> str:
    props = parameters.get("properties", {})
    required = parameters.get("required", [])
    parts = []
    for name, spec in props.items():
        t = spec.get("type", "any")
        enum = spec.get("enum")
        mark = "*" if name in required else ""
        if enum:
            parts.append(f"{name}{mark}({t}:{'/'.join(str(e) for e in enum)})")
        else:
            parts.append(f"{name}{mark}({t})")
    return ", ".join(parts) if parts else "无"


class ToolRegistry:
    """进程内工具池：注册 / 查询 / 清单生成"""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"工具已存在: {spec.name}")
        self._tools[spec.name] = spec
        logger.info(f"工具注册成功: {spec.name} (risk={spec.risk_level})")
        return spec

    def register_many(self, specs: list[ToolSpec]) -> None:
        for s in specs:
            self.register(s)

    def unregister(self, name: str) -> None:
        if name in self._tools:
            del self._tools[name]
            logger.info(f"工具已卸载: {name}")

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return [s for s in self._tools.values() if s.enabled]

    def list_all(self) -> list[ToolSpec]:
        """含已停用工具（管理界面用）"""
        return list(self._tools.values())

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """启停工具（管理界面热切换，无需重启）"""
        spec = self._tools.get(name)
        if spec is None:
            return False
        spec.enabled = enabled
        logger.info(f"工具状态切换: {name} -> {'启用' if enabled else '停用'}")
        return True

    def names(self) -> list[str]:
        return [s.name for s in self.all()]

    def summary(self) -> str:
        """完整工具清单（Router system prompt 注入）"""
        if not self.all():
            return "（当前无可用工具）"
        return "\n".join(s.summarize() for s in self.all())

    def to_mcp_schemas(self) -> list[dict]:
        """转 MCP 工具声明（name/description/inputSchema）"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "inputSchema": s.parameters,
            }
            for s in self.all()
        ]


# 全局工具池单例
registry = ToolRegistry()
