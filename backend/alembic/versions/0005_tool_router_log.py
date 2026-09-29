"""0005 工具调用（P2.1）：chat_router_logs 增加工具审计字段
- tool_names：本次路由选用的工具名（逗号分隔）
- tool_results：工具结果条数
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_router_logs ADD COLUMN IF NOT EXISTS tool_names VARCHAR(200)")
    op.execute("ALTER TABLE chat_router_logs ADD COLUMN IF NOT EXISTS tool_results INT DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_router_logs DROP COLUMN IF EXISTS tool_results")
    op.execute("ALTER TABLE chat_router_logs DROP COLUMN IF EXISTS tool_names")
