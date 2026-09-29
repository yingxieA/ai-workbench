"""0008 系统配置（KV）：system_configs 表 + 初始种子
- 运营可在管理界面热更新，无需改代码重启（同 rule_keywords 缓存模式）
- 当前键：prefilter_light_enabled（L2 轻量模型闲聊分类开关，默认 false）
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS system_configs (
            key VARCHAR(64) PRIMARY KEY,
            value TEXT NOT NULL,
            description VARCHAR(200),
            updated_by VARCHAR(64),
            updated_at TIMESTAMP DEFAULT now()
        )
    """)

    # 初始种子（幂等）
    op.execute("""
        INSERT INTO system_configs (key, value, description)
        SELECT t.key, t.value, t.description
        FROM (VALUES
            ('prefilter_light_enabled', 'false',
             'L2 轻量模型闲聊分类开关（true=开启，短闲聊走 qwen-turbo 直接应答，省主模型）')
        ) AS t(key, value, description)
        WHERE NOT EXISTS (SELECT 1 FROM system_configs s WHERE s.key = t.key)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS system_configs")
