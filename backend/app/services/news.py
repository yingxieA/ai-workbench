"""AI 日报服务 - Apify 抓取 + LLM 摘要"""
import os
import json
from datetime import date
from sqlalchemy import text
from fastapi.concurrency import run_in_threadpool
from apify_client.errors import ApifyApiError

from app.database import SessionLocal
from app.services.llm import chat_stream
from app.utils.logger import get_logger
from app.config import settings

logger = get_logger("news")
APIFY_TOKEN = settings.APIFY_API_KEY


def fetch_daily_ai_news() -> list:
    """
    通过 Apify 抓取 AI 新闻（同步阻塞函数）。
    在生产级设计中，该函数会被 run_in_threadpool 调度执行。
    """
    from apify_client import ApifyClient

    logger.info("开始 Apify 抓取 AI 新闻")
    client = ApifyClient(APIFY_TOKEN)

    # 注意：Apify 的 timeRange 参数限制只能是 today / week / month / all
    run_input = {
        "keywords": ["AI", "LLM", "Agent", "RAG", "Dify"],
        "totalMaxArticles": 10,
        "includeSummary": True,
        "timeRange": "today",
    }

    logger.info(f"调用 Apify actor, run_input={run_input}")
    try:
        # call() 返回的是 ActorRun 对象，不是字典
        run = client.actor("code-node-tools/ai-news-updates-api").call(run_input=run_input)

        # 用属性访问 default_dataset_id（下划线命名）
        dataset_id = run.default_dataset_id
        logger.info(f"Apify 运行完成, datasetId={dataset_id}")

        items = list(client.dataset(dataset_id).iterate_items())
        logger.info(f"Apify 抓取到 {len(items)} 条新闻")
        return items
    except ApifyApiError as e:
        logger.error(f"Apify API 调用失败: {e}")
        return []
    except Exception as e:
        logger.error(f"Apify 抓取发生未知异常: {e}", exc_info=True)
        return []


async def generate_daily_news() -> str:
    """生成今日日报（异步接口）"""
    logger.info("开始生成 AI 日报")

    # 1. 将同步的网络请求丢入线程池，防止阻塞 FastAPI 事件循环
    articles = await run_in_threadpool(fetch_daily_ai_news)

    if not articles:
        logger.warning("今日未获取到任何新闻")
        return "今日未获取到新闻，请稍后重试。"

    # 2. 构造 Prompt（注入当前日期，强制 LLM 使用正确日期）
    today_str = date.today().strftime("%Y年%m月%d日")

    article_titles = "\n".join([
        f"- [{a.get('source', 'AI')}] {a.get('title', '')} - {str(a.get('summary', ''))[:80]}..."
        for a in articles[:15]
    ])

    prompt = f"""你是一个 AI 领域的资深技术编辑。请将以下今日（{today_str}）AI 新闻整理成一份简洁的日报摘要。
要求：
1. **必须使用 {today_str} 作为日报标题的日期**，例如标题格式为「AI Daily Brief · {today_str}」。绝对不要自己编造或推测其他日期。
2. 按重要性排序，分为「大模型进展」、「AI 应用」和「开源与工具」三个板块。
3. 每条新闻用一句话概括，保留技术细节。
4. 结尾附上一句趋势总结。

今日新闻列表：
{article_titles}
"""

    # 3. 调用 LLM 生成摘要
    summary = ""
    try:
        for token in chat_stream(prompt):
            summary += token
    except Exception as e:
        logger.error(f"LLM 生成日报失败: {e}")
        return "AI 日报生成失败，请检查 LLM 服务。"

    # 4. 存库
    def _save_to_db():
        db = SessionLocal()
        try:
            db.execute(
                text("""
                    INSERT INTO daily_news (news_date, summary, articles)
                    VALUES (:d, :s, CAST(:a AS JSON))
                    ON CONFLICT (news_date) DO UPDATE SET summary = EXCLUDED.summary
                """),
                {"d": date.today(), "s": summary, "a": json.dumps(articles, ensure_ascii=False)}
            )
            db.commit()
            logger.info("AI 日报已成功写入数据库")
        except Exception as e:
            logger.error(f"日报存库失败: {e}")
            db.rollback()
        finally:
            db.close()

    await run_in_threadpool(_save_to_db)
    return summary


def get_today_news() -> dict:
    """获取今日日报（同步查询，供 API 调用）"""
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT summary, articles FROM daily_news WHERE news_date = :d AND is_deleted = false"),
            {"d": date.today()}
        ).fetchone()
        if row:
            return {"summary": row[0], "articles": row[1]}
        return {"summary": "", "articles": []}
    finally:
        db.close()