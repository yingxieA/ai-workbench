# -*- coding: utf-8 -*-
"""0013 内置工具种子入库：把 9 个内置工具（web_search/web_fetch/http_request/calculator/get_current_time/run_code/memory/date_calculator/knowledge_base_query）落 agent_tools，
使 DB 成为工具配置唯一权威源：管理界面启停、重启状态恢复与外部 MCP 语义一致。"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# name, description, risk_level, source
_BUILTIN_TOOLS = [
    ("web_search", "联网搜索：需要实时信息、最新消息、网络内容检索时使用", "normal"),
    ("web_fetch", "抓取网页内容：给定 URL 获取页面正文/结构化内容", "normal"),
    ("http_request", "调用任意 REST API：需要查询/操作外部 HTTP 接口时使用", "high"),
    ("calculator", "数学计算：任何需要精确算术/数学函数求值的问题", "normal"),
    ("get_current_time", "获取当前日期时间：涉及今天/现在/几号/星期几/时区的问题", "normal"),
    ("run_code", "在隔离沙箱中执行 Python 代码并返回结果（执行需人工确认）", "high"),
    ("memory", "读写用户级长期记忆（按用户隔离）：记住用户偏好、身份、上下文", "normal"),
    ("date_calculator", "日期推算：今天/明天/昨天/几天后是哪天、日期加减", "normal"),
    ("knowledge_base_query", "查询私有知识库（RAG 检索器）：需要引用库内资料回答时使用", "normal"),
]


def upgrade() -> None:
    for name, desc, risk in _BUILTIN_TOOLS:
        op.execute(
            f"""
            INSERT INTO agent_tools (name, description, risk_level, source, enabled, created_at)
            VALUES ('{name}', '{desc.replace("'", "''")}', '{risk}', 'builtin', TRUE, now())
            ON CONFLICT (name) DO UPDATE
              SET description = EXCLUDED.description,
                  risk_level = EXCLUDED.risk_level,
                  source = EXCLUDED.source
            """
        )


def downgrade() -> None:
    names = ", ".join(f"'{n}'" for n, _, _ in _BUILTIN_TOOLS)
    op.execute(f"DELETE FROM agent_tools WHERE name IN ({names}) AND source = 'builtin'")
