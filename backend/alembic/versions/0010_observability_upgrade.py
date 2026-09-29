# -*- coding: utf-8 -*-
"""0010 可观测与评估深化（P2.6）：在线反馈 + 幻觉检测 + 成本监控
- chat_messages 增 feedback / feedback_reason：在线点赞点踩落库（入训练数据底座）
- chat_router_logs 增 hallucination_verdict / hallucination_score / judge_reason：LLM as a Judge 结果
- chat_router_logs 增 cost：单次请求成本（token_usage × 单价）
- 成本配置种子：model_pricing（各模型单价）/ daily_cost_limit（日预算）/ cost_guard_enabled
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # chat_messages：在线反馈
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS feedback VARCHAR(20)")
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS feedback_reason TEXT")

    # chat_router_logs：幻觉检测 + 成本
    op.execute("ALTER TABLE chat_router_logs ADD COLUMN IF NOT EXISTS hallucination_verdict VARCHAR(20)")
    op.execute("ALTER TABLE chat_router_logs ADD COLUMN IF NOT EXISTS hallucination_score FLOAT")
    op.execute("ALTER TABLE chat_router_logs ADD COLUMN IF NOT EXISTS judge_reason TEXT")
    op.execute("ALTER TABLE chat_router_logs ADD COLUMN IF NOT EXISTS cost FLOAT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_router_logs_feedback ON chat_messages (feedback)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_router_logs_verdict ON chat_router_logs (hallucination_verdict)")

    # 成本配置种子（幂等）：单价（元 / 1K tokens）+ 日预算（元）
    op.execute("""
        INSERT INTO system_configs (key, value, description)
        SELECT t.key, t.value, t.description
        FROM (VALUES
            ('model_pricing',
             '{"qwen-max": 0.02, "qwen-plus": 0.005, "qwen-turbo": 0.0005, "deepseek-chat": 0.002}',
             '模型单价（元/1K tokens），成本核算用，JSON KV'),
            ('daily_cost_limit', '1.0',
             '日成本预算（元）：当日累计调用成本超过该值后，后续请求自动降档（跳过最高档模型）'),
            ('cost_guard_enabled', 'true',
             '成本超限自动降级开关（true=超预算自动切低档模型；false=不干预）')
        ) AS t(key, value, description)
        WHERE NOT EXISTS (SELECT 1 FROM system_configs s WHERE s.key = t.key)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS feedback")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS feedback_reason")
    op.execute("ALTER TABLE chat_router_logs DROP COLUMN IF EXISTS hallucination_verdict")
    op.execute("ALTER TABLE chat_router_logs DROP COLUMN IF EXISTS hallucination_score")
    op.execute("ALTER TABLE chat_router_logs DROP COLUMN IF EXISTS judge_reason")
    op.execute("ALTER TABLE chat_router_logs DROP COLUMN IF EXISTS cost")
