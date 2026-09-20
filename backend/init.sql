-- ============================================================
-- AI Workbench 数据库初始化脚本 v3
-- PostgreSQL 16 + pgvector
-- ============================================================

-- 启用扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================
-- DROP 旧表
-- ============================================================
DROP TABLE IF EXISTS learning_paths CASCADE;
DROP TABLE IF EXISTS daily_news CASCADE;
DROP TABLE IF EXISTS skills CASCADE;
DROP TABLE IF EXISTS chunks CASCADE;
DROP TABLE IF EXISTS documents CASCADE;

-- ============================================================
-- 文档表
-- ============================================================
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(500) NOT NULL,
    category        VARCHAR(100),
    tags            JSONB DEFAULT '[]'::jsonb,
    source          VARCHAR(50) NOT NULL,
    file_path       VARCHAR(500),
    md5_hash        VARCHAR(32) NOT NULL,
    model_version   VARCHAR(20) NOT NULL,
    user_id         UUID,
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    deleted_at      TIMESTAMP,
    UNIQUE(md5_hash)
);

CREATE INDEX idx_documents_category ON documents(category) WHERE is_deleted = FALSE;
CREATE INDEX idx_documents_tags ON documents USING GIN(tags);
CREATE INDEX idx_documents_md5 ON documents(md5_hash);

-- ============================================================
-- 文档块表
-- ============================================================
CREATE TABLE chunks (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id              UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_chunk_id     UUID,
    chunk_index         INT NOT NULL,
    page_no             INT,
    bbox               JSONB,
    chunk_type          VARCHAR(30) NOT NULL,
    source_type         VARCHAR(30) NOT NULL,
    content             TEXT NOT NULL,
    embedding           vector(1024),
    model_version       VARCHAR(20) NOT NULL,
    is_deleted          BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW(),
    deleted_at          TIMESTAMP,
    UNIQUE(doc_id, chunk_index)
);

CREATE INDEX idx_chunks_embedding ON chunks USING HNSW(embedding vector_cosine_ops);
CREATE INDEX idx_chunks_parent ON chunks(parent_chunk_id);
CREATE INDEX idx_chunks_doc ON chunks(doc_id) WHERE is_deleted = FALSE;

-- ============================================================
-- 技能表
-- ============================================================
CREATE TABLE skills (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(200) NOT NULL,
    category        VARCHAR(100),
    priority        INT DEFAULT 1,
    status          VARCHAR(30) DEFAULT 'todo',
    resource_url    VARCHAR(500),
    notes           TEXT,
    user_id         UUID,
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    deleted_at      TIMESTAMP
);

CREATE INDEX idx_skills_status ON skills(status) WHERE is_deleted = FALSE;
CREATE INDEX idx_skills_category ON skills(category);

-- ============================================================
-- 日报表
-- ============================================================
CREATE TABLE daily_news (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    news_date       DATE NOT NULL UNIQUE,
    summary         TEXT,
    articles        JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 学习路径表
-- ============================================================
CREATE TABLE learning_paths (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    goal            VARCHAR(200) NOT NULL,
    nodes           JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 触发器：自动更新 updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_documents_updated BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_chunks_updated BEFORE UPDATE ON chunks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_skills_updated BEFORE UPDATE ON skills
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_daily_news_updated BEFORE UPDATE ON daily_news
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_learning_paths_updated BEFORE UPDATE ON learning_paths
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- 中文注释
-- ============================================================
COMMENT ON TABLE documents IS '文档主表';
COMMENT ON COLUMN documents.id IS '主键UUID';
COMMENT ON COLUMN documents.title IS '文档标题';
COMMENT ON COLUMN documents.category IS '分类';
COMMENT ON COLUMN documents.tags IS '标签JSON数组';
COMMENT ON COLUMN documents.source IS '来源：markdown/pdf/lark/jupyter';
COMMENT ON COLUMN documents.file_path IS '原始文件路径';
COMMENT ON COLUMN documents.md5_hash IS '文件MD5增量检测';
COMMENT ON COLUMN documents.model_version IS 'embedding模型版本';
COMMENT ON COLUMN documents.user_id IS '用户ID预留';
COMMENT ON COLUMN documents.is_deleted IS '软删除标记';
COMMENT ON COLUMN documents.created_at IS '创建时间';
COMMENT ON COLUMN documents.updated_at IS '更新时间';
COMMENT ON COLUMN documents.deleted_at IS '删除时间';

COMMENT ON TABLE chunks IS '文档块表';
COMMENT ON COLUMN chunks.id IS '主键UUID';
COMMENT ON COLUMN chunks.doc_id IS '关联文档ID';
COMMENT ON COLUMN chunks.parent_chunk_id IS '父块ID';
COMMENT ON COLUMN chunks.chunk_index IS '块序号';
COMMENT ON COLUMN chunks.page_no IS 'PDF页码';
COMMENT ON COLUMN chunks.bbox IS 'PDF位置坐标';
COMMENT ON COLUMN chunks.chunk_type IS '块类型：text/table/image_caption/code/output';
COMMENT ON COLUMN chunks.source_type IS '来源：markdown/jupyter_code/jupyter_output';
COMMENT ON COLUMN chunks.content IS '块文本内容';
COMMENT ON COLUMN chunks.embedding IS 'bge-m3向量1024维';
COMMENT ON COLUMN chunks.model_version IS 'embedding模型版本';
COMMENT ON COLUMN chunks.is_deleted IS '软删除标记';
COMMENT ON COLUMN chunks.created_at IS '创建时间';
COMMENT ON COLUMN chunks.updated_at IS '更新时间';
COMMENT ON COLUMN chunks.deleted_at IS '删除时间';

COMMENT ON TABLE skills IS '技能表';
COMMENT ON COLUMN skills.id IS '主键UUID';
COMMENT ON COLUMN skills.name IS '技能名称';
COMMENT ON COLUMN skills.category IS '分类';
COMMENT ON COLUMN skills.priority IS '优先级1-5';
COMMENT ON COLUMN skills.status IS '状态：todo/learning/done';
COMMENT ON COLUMN skills.resource_url IS '学习链接';
COMMENT ON COLUMN skills.notes IS '笔记';
COMMENT ON COLUMN skills.user_id IS '用户ID预留';
COMMENT ON COLUMN skills.is_deleted IS '软删除标记';
COMMENT ON COLUMN skills.created_at IS '创建时间';
COMMENT ON COLUMN skills.updated_at IS '更新时间';
COMMENT ON COLUMN skills.deleted_at IS '删除时间';

COMMENT ON TABLE daily_news IS 'AI日报表';
COMMENT ON COLUMN daily_news.id IS '主键UUID';
COMMENT ON COLUMN daily_news.news_date IS '日报日期';
COMMENT ON COLUMN daily_news.summary IS 'AI总结内容';
COMMENT ON COLUMN daily_news.articles IS '原始文章列表';
COMMENT ON COLUMN daily_news.created_at IS '创建时间';
COMMENT ON COLUMN daily_news.updated_at IS '更新时间';

COMMENT ON TABLE learning_paths IS '学习路径表';
COMMENT ON COLUMN learning_paths.id IS '主键UUID';
COMMENT ON COLUMN learning_paths.goal IS '目标岗位';
COMMENT ON COLUMN learning_paths.nodes IS '学习路径树';
COMMENT ON COLUMN learning_paths.created_at IS '创建时间';
COMMENT ON COLUMN learning_paths.updated_at IS '更新时间';


ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector;
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);

-- ============================================================
-- 对话表
-- ============================================================
CREATE TABLE IF NOT EXISTS chat_sessions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title       VARCHAR(200) DEFAULT '新对话',
    pinned      BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL,
    content     TEXT NOT NULL,
    feedback    VARCHAR(20),
    feedback_reason VARCHAR(200),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);

COMMENT ON TABLE chat_sessions IS '对话会话表';
COMMENT ON TABLE chat_messages IS '对话消息表';
-- ============================================================
-- PDF 异步任务表
-- ============================================================
CREATE TABLE IF NOT EXISTS pdf_tasks (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename    VARCHAR(500),
    status      VARCHAR(20) DEFAULT 'pending',
    chunks      INT DEFAULT 0,
    error       TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);


-- 技能表扩展字段
ALTER TABLE skills ADD COLUMN IF NOT EXISTS goal TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS sub_tasks JSONB DEFAULT '[]'::jsonb;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS resources JSONB DEFAULT '[]'::jsonb;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS verification TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS progress INTEGER DEFAULT 0;



-- ============================================================
-- 技能子任务表
-- ============================================================
CREATE TABLE IF NOT EXISTS skill_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    skill_id UUID REFERENCES skills(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    sort_order INTEGER DEFAULT 0,
    completed BOOLEAN DEFAULT false,
    guide JSONB DEFAULT '[]'::jsonb,
    resources JSONB DEFAULT '[]'::jsonb,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_skill_tasks_skill_id ON skill_tasks(skill_id);


-- skill_tasks 逻辑删除字段
ALTER TABLE skill_tasks ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE skill_tasks ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;



-- learning_paths 逻辑删除字段
ALTER TABLE learning_paths ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE learning_paths ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;



-- 对话分享表
CREATE TABLE IF NOT EXISTS chat_shares (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    share_token VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP
);



-- chat_shares 逻辑删除字段
ALTER TABLE chat_shares ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE chat_shares ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;



-- 复习表（艾宾浩斯遗忘曲线）
CREATE TABLE IF NOT EXISTS review_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type VARCHAR(50),
    source_id UUID,
    title VARCHAR(200),
    content TEXT,
    review_count INT DEFAULT 0,
    next_review_at TIMESTAMP DEFAULT NOW(),
    is_deleted BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);



-- 逻辑删除字段补全
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE daily_news ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE daily_news ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE github_projects ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE github_projects ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE github_teardowns ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE github_teardowns ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE pdf_tasks ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE pdf_tasks ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;



-- 日报任务表
CREATE TABLE IF NOT EXISTS news_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status VARCHAR(20) DEFAULT 'pending',
    error TEXT,
    summary TEXT,
    is_deleted BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);

