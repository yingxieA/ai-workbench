"""基线迁移：固化现有全部表结构 + P0 用户隔离（chat_sessions.user_id）

- 幂等写法（IF NOT EXISTS）：现有库执行安全，新库执行可完整建表
- 补齐 github_projects / github_teardowns / travel_plans 建表（原 init.sql 缺失）
- chat_sessions 新增 user_id（用户隔离，修复历史对话串号问题）
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- 扩展（幂等） ----------
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    # ---------- 用户表 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR PRIMARY KEY,
            username VARCHAR UNIQUE NOT NULL,
            password_hash VARCHAR NOT NULL,
            nickname VARCHAR,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- 文档 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            title VARCHAR(500) NOT NULL,
            category VARCHAR(100),
            tags JSONB DEFAULT '[]'::jsonb,
            source VARCHAR(50) NOT NULL,
            file_path VARCHAR(500),
            md5_hash VARCHAR(32) NOT NULL,
            model_version VARCHAR(20) NOT NULL,
            user_id UUID,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP,
            UNIQUE(md5_hash)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category) WHERE is_deleted = FALSE")
    op.execute("CREATE INDEX IF NOT EXISTS idx_documents_tags ON documents USING GIN(tags)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_documents_md5 ON documents(md5_hash)")

    # ---------- 文档块 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            doc_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            parent_chunk_id UUID,
            chunk_index INT NOT NULL,
            page_no INT,
            bbox JSONB,
            chunk_type VARCHAR(30) NOT NULL,
            source_type VARCHAR(30) NOT NULL,
            content TEXT NOT NULL,
            embedding vector(1024),
            tsv TEXT,
            model_version VARCHAR(20) NOT NULL,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP,
            UNIQUE(doc_id, chunk_index)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING HNSW(embedding vector_cosine_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_parent ON chunks(parent_chunk_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id) WHERE is_deleted = FALSE")

    # ---------- 技能 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name VARCHAR(200) NOT NULL,
            category VARCHAR(100),
            priority INT DEFAULT 1,
            status VARCHAR(30) DEFAULT 'todo',
            resource_url VARCHAR(500),
            notes TEXT,
            goal TEXT,
            sub_tasks JSONB DEFAULT '[]'::jsonb,
            resources JSONB DEFAULT '[]'::jsonb,
            verification TEXT,
            progress INTEGER DEFAULT 0,
            user_id UUID,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status) WHERE is_deleted = FALSE")
    op.execute("CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category)")

    # ---------- 技能子任务 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS skill_tasks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            skill_id UUID REFERENCES skills(id) ON DELETE CASCADE,
            title VARCHAR(200) NOT NULL,
            sort_order INTEGER DEFAULT 0,
            completed BOOLEAN DEFAULT false,
            guide JSONB DEFAULT '[]'::jsonb,
            resources JSONB DEFAULT '[]'::jsonb,
            notes TEXT,
            is_deleted BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_skill_tasks_skill_id ON skill_tasks(skill_id)")

    # ---------- 学习路径 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS learning_paths (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            goal VARCHAR(200) NOT NULL,
            nodes JSONB DEFAULT '[]'::jsonb,
            completed_nodes JSONB DEFAULT '[]'::jsonb,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- AI 日报 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_news (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            news_date DATE NOT NULL UNIQUE,
            summary TEXT,
            articles JSONB DEFAULT '[]'::jsonb,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- 旅游规划 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel_plans (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            destination VARCHAR(100) NOT NULL,
            origin VARCHAR(100),
            start_date VARCHAR(20),
            days INT DEFAULT 3,
            people INT DEFAULT 2,
            budget VARCHAR(50) DEFAULT '中等',
            preferences VARCHAR(200),
            plan_data JSONB DEFAULT '{}'::jsonb,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- 对话会话（P0：用户隔离） ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id VARCHAR(64),
            title VARCHAR(200) DEFAULT '新对话',
            pinned BOOLEAN DEFAULT FALSE,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)
    # 存量库补 user_id 列 + 索引 + 外键
    op.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id VARCHAR(64)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_id ON chat_sessions(user_id)")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_chat_sessions_user_id') THEN
                ALTER TABLE chat_sessions ADD CONSTRAINT fk_chat_sessions_user_id
                    FOREIGN KEY (user_id) REFERENCES users(id);
            END IF;
        END $$;
    """)

    # ---------- 对话消息 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            feedback VARCHAR(20),
            feedback_reason VARCHAR(200),
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages(session_id, created_at)")

    # ---------- 对话分享 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_shares (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
            share_token VARCHAR(64) UNIQUE NOT NULL,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)

    # ---------- PDF 异步任务 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS pdf_tasks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            filename VARCHAR(500),
            status VARCHAR(20) DEFAULT 'pending',
            chunks INT DEFAULT 0,
            error TEXT,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- 日报任务 ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_tasks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            status VARCHAR(20) DEFAULT 'pending',
            error TEXT,
            summary TEXT,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- 复习（艾宾浩斯） ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS review_items (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            source_type VARCHAR(50),
            source_id UUID,
            title VARCHAR(200),
            content TEXT,
            review_count INT DEFAULT 0,
            next_review_at TIMESTAMP DEFAULT NOW(),
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- GitHub 缓存表（原 init.sql 缺失的建表） ----------
    op.execute("""
        CREATE TABLE IF NOT EXISTS github_projects (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name VARCHAR(200) NOT NULL UNIQUE,
            detail TEXT,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS github_teardowns (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name VARCHAR(200) NOT NULL UNIQUE,
            detail TEXT,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            deleted_at TIMESTAMP
        )
    """)

    # ---------- updated_at 触发器 ----------
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in [
        "documents",
        "chunks",
        "skills",
        "skill_tasks",
        "learning_paths",
        "daily_news",
        "travel_plans",
        "chat_sessions",
        "chat_shares",
        "pdf_tasks",
        "news_tasks",
        "review_items",
        "github_projects",
        "github_teardowns",
    ]:
        op.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_{table}_updated') THEN
                    CREATE TRIGGER trg_{table}_updated BEFORE UPDATE ON {table}
                        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
                END IF;
            END $$;
        """)


def downgrade() -> None:
    # 回滚 P0：删除 user_id 隔离（保留存量数据表）
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_user_id")
    op.execute("ALTER TABLE chat_sessions DROP CONSTRAINT IF EXISTS fk_chat_sessions_user_id")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS user_id")
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_session_created")
    # 回滚补建的表（原 init.sql 缺失、本次迁移补建的）
    op.execute("DROP TABLE IF EXISTS github_teardowns")
    op.execute("DROP TABLE IF EXISTS github_projects")
    op.execute("DROP TABLE IF EXISTS travel_plans")
