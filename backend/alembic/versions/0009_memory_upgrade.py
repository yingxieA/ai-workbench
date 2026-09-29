# -*- coding: utf-8 -*-
"""0009 记忆与多轮对话（P2.5）：session_summaries + user_profiles + 记忆相关系统配置
- session_summaries：短期记忆压缩摘要（滑动窗口外的增量压缩，version 累积）
- user_profiles：长期记忆用户画像（对话抽取，JSONB 结构化）
- 配置：chat_window_rounds / chat_summary_enabled / chat_redis_cache_enabled
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS session_summaries (
            session_id UUID PRIMARY KEY,
            summary TEXT NOT NULL DEFAULT '',
            version INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id VARCHAR(64) PRIMARY KEY,
            profile_json JSONB NOT NULL DEFAULT '{}',
            version INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT now()
        )
    """)

    # 记忆相关配置种子（幂等）
    op.execute("""
        INSERT INTO system_configs (key, value, description)
        SELECT t.key, t.value, t.description
        FROM (VALUES
            ('chat_window_rounds', '10',
             '短期记忆滑动窗口轮数：窗口内原始消息直传，窗口外走 LLM 摘要压缩'),
            ('chat_summary_enabled', 'true',
             '会话摘要压缩开关（true=溢出消息自动摘要，防 token 超限）'),
            ('chat_redis_cache_enabled', 'true',
             '会话 Redis 热缓存开关（会话恢复快读，miss 回源 PG 回填）')
        ) AS t(key, value, description)
        WHERE NOT EXISTS (SELECT 1 FROM system_configs s WHERE s.key = t.key)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_summaries")
    op.execute("DROP TABLE IF EXISTS user_profiles")
