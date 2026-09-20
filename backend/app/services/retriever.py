"""检索服务：向量召回 + 父子块回表"""
from sqlalchemy import text
from app.database import SessionLocal
from app.services.embedding import embed_texts
from app.utils.logger import get_logger

logger = get_logger("retriever")


def vector_search(query: str, top_k: int = 50) -> list[dict]:
    """向量召回（pgvector cosine）"""
    q_vec = embed_texts([query])[0]
    db = SessionLocal()
    try:
        sql = """SELECT c.id, c.parent_chunk_id, c.doc_id, c.content, d.title
                FROM chunks c
                JOIN documents d ON c.doc_id = d.id
                WHERE c.is_deleted = FALSE"""
        params = {"vec": str(q_vec), "k": top_k}
        sql += " ORDER BY c.embedding <=> :vec LIMIT :k"
        rows = db.execute(text(sql), params).fetchall()
        logger.info(f"向量召回 {len(rows)} 条")
        return [{"chunk_id": str(r[0]), "parent_id": str(r[1]) if r[1] else None,
                 "doc_id": str(r[2]), "content": r[3], "doc_title": r[4]} for r in rows]
    finally:
        db.close()


def fetch_parent_contents(chunks: list[dict]) -> list[dict]:
    """命中子块后回表查父块内容"""
    # 收集所有 parent_id
    parent_ids = list({c["parent_id"] for c in chunks if c["parent_id"]})
    if not parent_ids:
        return chunks

    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT id, content FROM chunks WHERE id = ANY(:ids)"),
            {"ids": parent_ids}
        ).fetchall()
        parent_map = {str(r[0]): r[1] for r in rows}

        # 用父块内容替换子块内容，截断到 1000 字
        result = []
        for c in chunks:
            pid = c["parent_id"]
            if pid and pid in parent_map:
                content = parent_map[pid][:1000]
                result.append({**c, "content": content})
            else:
                result.append(c)
        return result
    finally:
        db.close()


def retrieve(query: str, top_k: int = 20) -> list[dict]:
    """向量召回 + 父子块回表"""
    logger.info(f"检索: {query}")
    candidates = vector_search(query, top_k=50)
    # 父子块回表
    candidates = fetch_parent_contents(candidates)
    logger.info(f"最终 {len(candidates[:top_k])} 条")
    return candidates[:top_k]
