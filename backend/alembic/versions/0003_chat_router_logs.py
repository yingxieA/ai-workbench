"""0003 智能问答 Agent 链路审计表：
- 新增 chat_router_logs：记录每次 Agent 请求的路由、模型、降级、耗时，用于审计与性能观测
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_router_logs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id VARCHAR(64),
            session_id UUID,
            question TEXT,
            route_path VARCHAR(20),
            route_detail VARCHAR(50),
            confidence_score FLOAT,
            model_used VARCHAR(50),
            fallback_triggered BOOLEAN DEFAULT FALSE,
            error_type VARCHAR(100),
            latency_ms INT,
            token_usage INT,
            created_at TIMESTAMP DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_router_logs_created_at ON chat_router_logs (created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_router_logs_route ON chat_router_logs (route_path)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_router_logs_user ON chat_router_logs (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_router_logs")
