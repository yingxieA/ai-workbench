# -*- coding: utf-8 -*-
"""0011 反馈复核状态：chat_messages 增 review_status（pending/approved/rejected）
在线反馈回流到训练数据前，需人工复核；复核通过（approved）的才允许导出训练集
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS review_status VARCHAR(20) DEFAULT 'pending'")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS review_status")
