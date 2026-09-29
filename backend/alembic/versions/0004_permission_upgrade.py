"""0004 文档管理 + 文档级/片段级权限升级：
- users 增加 role_level（权限等级：100=admin / 50=高级 / 10=普通）
- documents 增加 visibility / min_level（文档级权限）
- chunks 增加 sensitivity / min_level / classify_reason（片段级敏感标记）
- 新增 rule_keywords（敏感词/分类关键词运营配置）
- 新增 audit_logs（审计日志）
- 存量 admin 账号提升为最高权限（username=admin）
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. users.role_level（默认普通 10）
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role_level INT DEFAULT 10")

    # 2. documents 文档级权限
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'internal'")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS min_level INT DEFAULT 10")

    # 3. chunks 片段级敏感标记
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS sensitivity VARCHAR(20) DEFAULT 'public'")
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS min_level INT DEFAULT 10")
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS classify_reason TEXT")

    # 4. rule_keywords 运营配置
    op.execute("""
        CREATE TABLE IF NOT EXISTS rule_keywords (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            category VARCHAR(30) NOT NULL,
            keyword VARCHAR(200) NOT NULL,
            level VARCHAR(20) DEFAULT 'secret',
            enabled BOOLEAN DEFAULT TRUE,
            created_by VARCHAR(64),
            created_at TIMESTAMP DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rule_keywords_category ON rule_keywords (category)")

    # 5. audit_logs 审计日志
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id VARCHAR(64),
            action VARCHAR(50) NOT NULL,
            target_id VARCHAR(64),
            detail JSONB,
            created_at TIMESTAMP DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_user ON audit_logs (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_created ON audit_logs (created_at DESC)")

    # 6. 存量 admin 账号提升为最高权限（幂等：只升不降）
    op.execute("UPDATE users SET role_level = 100 WHERE username = 'admin' AND role_level < 100")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs")
    op.execute("DROP TABLE IF EXISTS rule_keywords")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS classify_reason")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS min_level")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS sensitivity")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS min_level")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS visibility")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS role_level")
