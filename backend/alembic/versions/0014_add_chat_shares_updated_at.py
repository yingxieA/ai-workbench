# -*- coding: utf-8 -*-
"""0014: chat_shares 补 updated_at 列（模型 ChatShare 有该字段，表结构缺列导致 INSERT 500）"""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_shares", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_shares", "updated_at")
