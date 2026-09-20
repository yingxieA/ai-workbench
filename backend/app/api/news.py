"""日报 API"""
from fastapi import APIRouter, BackgroundTasks
import httpx
from app.services.news import generate_daily_news, get_today_news
from app.services.llm import chat_stream
from app.utils.logger import get_logger
from datetime import date

logger = get_logger("news_api")

router = APIRouter(prefix="/api/news", tags=["news"])

# 项目介绍缓存
_detail_cache = {}

# GitHub 周榜缓存（内存缓存，每天更新一次）
_trending_cache = {
    "date": "",
    "data": []
}


@router.get("/today")
def today_news():
    return get_today_news()


@router.post("/generate")
async def generate(background_tasks: BackgroundTasks):
    """提交日报生成任务，立即返回 task_id"""
    import uuid
    from sqlalchemy import text
    from app.database import SessionLocal

    task_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.execute(text("INSERT INTO news_tasks (id, status) VALUES (:id, 'pending')"), {"id": task_id})
        db.commit()
    finally:
        db.close()

    async def run_task():
        db2 = SessionLocal()
        try:
            db2.execute(text("UPDATE news_tasks SET status='processing' WHERE id=:id"), {"id": task_id})
            db2.commit()
            summary = await generate_daily_news()
            db2.execute(text("UPDATE news_tasks SET status='done', summary=:s WHERE id=:id"), {"s": summary, "id": task_id})
            db2.commit()
        except Exception as e:
            logger.error(f"日报任务失败: {e}")
            db2.execute(text("UPDATE news_tasks SET status='failed', error=:e WHERE id=:id"), {"e": str(e), "id": task_id})
            db2.commit()
        finally:
            db2.close()

    background_tasks.add_task(run_task)
    return {"task_id": task_id, "status": "pending"}


@router.get("/task/{task_id}")
def get_task(task_id: str):
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT status, summary, error FROM news_tasks WHERE id=:id"),
            {"id": task_id}
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        return {"status": row[0], "summary": row[1] or "", "error": row[2]}
    finally:
        db.close()


@router.get("/github-trending")
async def github_trending(refresh: bool = False):
    """GitHub AI 项目周榜（带内存缓存，每天更新一次）"""
    today = date.today().isoformat()

    # 1. 如果缓存是今天的且不是强制刷新，直接返回缓存
    if not refresh and _trending_cache["date"] == today and _trending_cache["data"]:
        return {"items": _trending_cache["data"]}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": "topic:ai created:>2025-09-10",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 10
                },
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            data = resp.json()
            items = data.get("items", [])
            descs = [repo["description"] or "" for repo in items]
            trans_prompt = f"把以下项目描述翻译成中文，每个一行：\n" + "\n".join(descs)
            trans_result = ""
            for token in chat_stream(trans_prompt):
                trans_result += token
            trans_list = trans_result.strip().split("\n")

            result = []
            for i, repo in enumerate(items):
                result.append({
                    "name": repo["full_name"],
                    "url": repo["html_url"],
                    "description": trans_list[i] if i < len(trans_list) else (repo["description"] or ""),
                    "stars": repo["stargazers_count"],
                    "language": repo["language"] or "",
                    "forks": repo["forks_count"]
                })

            # 2. 更新缓存
            _trending_cache["date"] = today
            _trending_cache["data"] = result

            return {"items": result}
    except Exception as e:
        logger.error(f"GitHub trending 失败: {e}")
        # 出错时如果有旧缓存，也返回旧缓存
        if _trending_cache["data"]:
            return {"items": _trending_cache["data"]}
        return {"items": []}


@router.get("/github-search")
async def github_search(q: str, per_page: int = 10):
    """搜索 GitHub 项目（AI 相关）"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": f"{q} in:name",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_page
                },
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            data = resp.json()
            items = data.get("items", [])
            result = []
            for repo in items:
                result.append({
                    "name": repo["full_name"],
                    "url": repo["html_url"],
                    "description": repo["description"] or "",
                    "stars": repo["stargazers_count"],
                    "language": repo["language"] or "",
                    "forks": repo["forks_count"]
                })
            return {"items": result}
    except Exception as e:
        logger.error(f"GitHub search 失败: {e}")
        return {"items": []}


@router.get("/github-project-teardown")
async def github_project_teardown(name: str):
    """AI 拆解：学习路线 + 核心代码"""
    from sqlalchemy import text
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT detail FROM github_teardowns WHERE name = :n"),
            {"n": name}
        ).fetchone()
        if row:
            return {"teardown": row[0]}
    finally:
        db.close()

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(f"https://api.github.com/repos/{name}")
            repo = resp.json()
            desc = repo.get("description", "")

            readme = ""
            for branch in ["main", "master"]:
                try:
                    r = await client.get(f"https://raw.githubusercontent.com/{name}/{branch}/README.md")
                    if r.status_code == 200:
                        readme = r.text[:3000]
                        break
                except:
                    pass

            prompt = f"""请深入分析这个 GitHub 项目：{name}

描述：{desc}
README：{readme}

请按以下格式输出，要具体到文件路径和命令：

## 📚 推荐学习路线
1. **第一步**：xxx（具体看哪个文件，如 docs/architecture.md）
2. **第二步**：xxx（具体跑什么命令，如 docker-compose up）
3. **第三步**：xxx（具体看哪个核心文件，如 src/agent.py）

## 💻 核心代码（3个关键片段）
```python
# 片段1：xxx
```

```python
# 片段2：xxx
```

```python
# 片段3：xxx
```

## 🎯 学习重点
- 重点 1
- 重点 2
- 重点 3

## ⚠️ 常见坑
- 坑 1：xxx
- 坑 2：xxx"""

            result = ""
            for token in chat_stream(prompt):
                result += token

            db = SessionLocal()
            try:
                db.execute(
                    text("INSERT INTO github_teardowns (name, detail) VALUES (:n, :d) ON CONFLICT (name) DO UPDATE SET detail = EXCLUDED.detail"),
                    {"n": name, "d": result}
                )
                db.commit()
            finally:
                db.close()
            return {"teardown": result}
    except Exception as e:
        logger.error(f"AI 拆解失败: {e}")
        return {"teardown": "拆解失败"}


@router.get("/github-project-detail")
async def github_project_detail(name: str):
    """生成项目介绍（PG 缓存）"""
    from sqlalchemy import text
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        # 1. 先查缓存
        row = db.execute(
            text("SELECT detail FROM github_projects WHERE name = :n"),
            {"n": name}
        ).fetchone()
        if row:
            return {"detail": row[0]}
    finally:
        db.close()

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # 1. 拉项目基本信息
            resp = await client.get(f"https://api.github.com/repos/{name}")
            repo = resp.json()
            desc = repo.get("description", "")
            lang = repo.get("language", "")
            stars = repo.get("stargazers_count", 0)

            # 2. 拉 README
            readme = ""
            for branch in ["main", "master"]:
                try:
                    r = await client.get(f"https://raw.githubusercontent.com/{name}/{branch}/README.md")
                    if r.status_code == 200:
                        readme = r.text[:2000]
                        break
                except:
                    pass

            prompt = f"""请基于以下 GitHub 项目信息，用中文介绍这个项目：{name}

基本信息：
- 描述：{desc}
- 主语言：{lang}
- Star 数：{stars}

README 内容：
{readme}

请按以下格式输出：
## 项目简介
（一句话说清楚做什么的）

## 技术架构
（基于 README 推测用了什么技术栈）

## 适合人群
（谁会用这个项目）

## 核心价值
（解决什么问题）"""

            result = ""
            for token in chat_stream(prompt):
                result += token
            # 存库
            db = SessionLocal()
            try:
                db.execute(
                    text("INSERT INTO github_projects (name, detail) VALUES (:n, :d) ON CONFLICT (name) DO UPDATE SET detail = EXCLUDED.detail"),
                    {"n": name, "d": result}
                )
                db.commit()
            finally:
                db.close()
            return {"detail": result}
    except Exception as e:
        logger.error(f"项目详情失败: {e}")
        return {"detail": "获取失败"}
