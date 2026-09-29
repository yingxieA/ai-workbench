"""0007 前置过滤规则（P2.3）：intent_rules 表 + 初始种子话术
- 精确 / 正则 / 关键词三层匹配的固定话术（问候 / 感谢 / 再见 / 身份 / 控制）
- DB 持久化 + Redis 缓存热更新（同 rule_keywords 模式）
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS intent_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trigger_type VARCHAR(20) NOT NULL,
            trigger VARCHAR(500) NOT NULL,
            reply_template TEXT NOT NULL,
            intent VARCHAR(30) DEFAULT 'chitchat',
            priority INTEGER DEFAULT 1,
            enabled BOOLEAN DEFAULT TRUE,
            created_by VARCHAR(64),
            created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_intent_rules_enabled ON intent_rules (enabled, priority DESC)
    """)

    # 初始种子话术（幂等：已存在同 trigger 的不重复插入）
    op.execute("""
        INSERT INTO intent_rules (trigger_type, trigger, reply_template, intent, priority)
        SELECT 'exact', t.trigger, t.reply, t.intent, t.priority
        FROM (VALUES
            ('你好', '你好！我是智能问答助手，可以帮你解答文档问题、调用工具、分析数据，有什么想问的吗？', 'chitchat', 10),
            ('您好', '你好！我是智能问答助手，可以帮你解答文档问题、调用工具、分析数据，有什么想问的吗？', 'chitchat', 10),
            ('hi', 'Hi！我是智能问答助手，有什么可以帮你的吗？', 'chitchat', 10),
            ('hello', 'Hello！我是智能问答助手，有什么可以帮你的吗？', 'chitchat', 10),
            ('嗨', '嗨！我在的，想聊点什么？', 'chitchat', 10),
            ('在吗', '在的，请问有什么可以帮你？', 'chitchat', 10),
            ('早上好', '早上好！今天有什么需要帮忙的吗？', 'chitchat', 10),
            ('下午好', '下午好！有什么可以帮你的吗？', 'chitchat', 10),
            ('晚上好', '晚上好！有什么可以帮你的吗？', 'chitchat', 10),
            ('谢谢', '不客气～还有什么可以帮你的吗？', 'chitchat', 8),
            ('感谢', '不客气～还有什么可以帮你的吗？', 'chitchat', 8),
            ('再见', '再见！有需要随时找我。', 'chitchat', 8),
            ('拜拜', '拜拜！有需要随时找我。', 'chitchat', 8),
            ('你是谁', '我是智能问答助手，可以帮你解答问题、查询文档、调用工具。', 'identity', 10),
            ('你叫什么', '我叫智能问答助手，可以帮你解答各类问题。', 'identity', 10),
            ('你是哪个公司的', '我是基于大模型的智能问答助手，服务于本工作台。', 'identity', 8),
            ('你能做什么', '我可以：① 回答你上传文档/知识库的问题（带引用）② 调用工具查天气、查路线、搜索、算数 ③ 拆解多步复杂任务。直接问我即可！', 'identity', 8)
        ) AS t(trigger, reply, intent, priority)
        WHERE NOT EXISTS (
            SELECT 1 FROM intent_rules r WHERE r.trigger_type = 'exact' AND r.trigger = t.trigger
        )
    """)

    op.execute("""
        INSERT INTO intent_rules (trigger_type, trigger, reply_template, intent, priority)
        SELECT 'regex', t.trigger, t.reply, t.intent, t.priority
        FROM (VALUES
            ('^(你好|您好|嗨|hi|hello)([!！。？]|\\s*)$', '你好！我是智能问答助手，有什么想问的吗？', 'chitchat', 5),
            ('^(谢谢|感谢)(你|啦|了)?([!！。]|\\s*)$', '不客气～还有什么可以帮你的吗？', 'chitchat', 5),
            ('^(再见|拜拜|88|886)([!！。]|\\s*)$', '再见！有需要随时找我。', 'chitchat', 5)
        ) AS t(trigger, reply, intent, priority)
        WHERE NOT EXISTS (
            SELECT 1 FROM intent_rules r WHERE r.trigger_type = 'regex' AND r.trigger = t.trigger
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intent_rules")
