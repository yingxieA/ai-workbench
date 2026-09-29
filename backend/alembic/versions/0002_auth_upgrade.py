"""P0+P1 多用户认证升级：
- users 表扩展：email/avatar/status/last_login_at/failed_login_count/locked_until
- 新增 refresh_tokens（refresh token 轮换与撤销）
- 新增 login_logs（登录审计）
- 用户隔离推广：documents/skills 的 user_id 对齐为 VARCHAR(64)；
  learning_paths/travel_plans/review_items 新增 user_id + 索引 + 外键
- 存量业务数据归并到管理员：69f929bd-e8ae-484c-9be6-7a76b43d01c6
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ADMIN_ID = "69f929bd-e8ae-484c-9be6-7a76b43d01c6"


def upgrade() -> None:
    # ---------- 1. users 表扩展 ----------
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(128)")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='users' AND indexname='users_email_key') THEN
                ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar VARCHAR(500)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INT DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")

    # ---------- 2. refresh_tokens 表 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id VARCHAR(64) NOT NULL REFERENCES users(id),
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_token_hash ON refresh_tokens(token_hash)")

    # ---------- 3. login_logs 审计表 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id VARCHAR(64),
            username VARCHAR(64),
            success BOOLEAN DEFAULT FALSE,
            ip VARCHAR(45),
            user_agent VARCHAR(300),
            reason VARCHAR(200),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_login_logs_user_id ON login_logs(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_login_logs_created_at ON login_logs(created_at)")

    # ---------- 4. 隔离推广：documents / skills 对齐 VARCHAR(64) ----------
    # documents.user_id 当前为 UUID（存量全 NULL），安全转换
    op.execute("ALTER TABLE documents ALTER COLUMN user_id TYPE VARCHAR(64) USING user_id::text")
    op.execute("ALTER TABLE skills ALTER COLUMN user_id TYPE VARCHAR(64) USING user_id::text")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_user_id ON documents(user_id) WHERE is_deleted = FALSE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_skills_user_id ON skills(user_id) WHERE is_deleted = FALSE")

    # ---------- 5. learning_paths / travel_plans / review_items 新增 user_id ----------
    for table in ["learning_paths", "travel_plans", "review_items"]:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id VARCHAR(64)")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table}(user_id)")
        op.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_{table}_user_id') THEN
                    ALTER TABLE {table} ADD CONSTRAINT fk_{table}_user_id
                        FOREIGN KEY (user_id) REFERENCES users(id);
                END IF;
            END $$;
        """)

    # ---------- 6. 存量业务数据归并到管理员 ----------
    for table in ["learning_paths", "travel_plans", "review_items"]:
        op.execute(f"UPDATE {table} SET user_id = '{ADMIN_ID}' WHERE user_id IS NULL")


def downgrade() -> None:
    # 业务表回滚 user_id
    for table in ["review_items", "travel_plans", "learning_paths"]:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS fk_{table}_user_id")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_user_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS user_id")
    # documents/skills 类型回退 UUID（数据为 NULL 或字符串，转回 UUID 仅当全部为 UUID 格式）
    op.execute("ALTER TABLE documents ALTER COLUMN user_id TYPE UUID USING NULLIF(user_id, '')::uuid")
    op.execute("ALTER TABLE skills ALTER COLUMN user_id TYPE UUID USING NULLIF(user_id, '')::uuid")
    op.execute("DROP INDEX IF EXISTS ix_documents_user_id")
    op.execute("DROP INDEX IF EXISTS ix_skills_user_id")
    # 认证表
    op.execute("DROP TABLE IF EXISTS login_logs")
    op.execute("DROP TABLE IF EXISTS refresh_tokens")
    # users 回滚
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key")
    for col in [
        "locked_until",
        "failed_login_count",
        "last_login_at",
        "status",
        "avatar",
        "email",
        "updated_at",
        "deleted_at",
    ]:
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {col}")
