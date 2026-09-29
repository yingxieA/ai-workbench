"""内置工具注册入口：启动时把 7 大基础工具 + 日期/知识库工具注入全局工具池"""

from app.agent.tools.registry import registry
from app.utils.logger import get_logger

logger = get_logger("tools_builtin")


def register_builtin_tools() -> None:
    from app.agent.tools.builtin.http_tools import register_http_tools
    from app.agent.tools.builtin.misc_tools import register_misc_tools
    from app.agent.tools.builtin.kb_tools import register_kb_tools

    register_http_tools(registry)
    register_misc_tools(registry)
    register_kb_tools(registry)
    logger.info(f"内置工具注册完成: {registry.names()}")
