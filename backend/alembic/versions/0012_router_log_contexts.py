# -*- coding: utf-8 -*-
"""0012 审计表存检索片段：chat_router_logs 增 contexts（幻觉检测可观测：回答 vs 片段对比）"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_router_logs ADD COLUMN IF NOT EXISTS contexts TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_router_logs DROP COLUMN IF EXISTS contexts")
