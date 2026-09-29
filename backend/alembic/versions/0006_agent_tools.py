"""0006 工具管理（P2.1 增强 / P2.2）：agent_tools 表
- 工具启停状态、外部 MCP 连接配置持久化（重启后自动恢复）
- name 主键；source: builtin / external；connect_config: 外部连接 {type, command, args, url}
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_tools (
            name VARCHAR(100) PRIMARY KEY,
            description TEXT,
            risk_level VARCHAR(20) DEFAULT 'normal',
            source VARCHAR(20) DEFAULT 'builtin',
            connect_config JSONB,
            enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_tools")
