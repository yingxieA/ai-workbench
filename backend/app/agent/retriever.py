"""LlamaIndex 检索节点：复用现有 bge-m3 向量 + PostgreSQL chunks 表

- ChunksVectorStore：LlamaIndex 自定义 VectorStore，底层走现有 pgvector 余弦 SQL（零数据迁移）
- BgeM3Embedding：复用现有 embed_texts 单例，不重复加载模型
- rag_retrieve 节点：LlamaIndex 召回 → 父子块回表 → bge-reranker 重排 → 阈值过滤
- 权限过滤：contextvar 注入 {user_id, role_level}，SQL 层按文档可见性 + 片段敏感等级过滤
"""

import threading
from contextvars import ContextVar
from typing import Optional

from llama_index.core import VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.vector_stores.types import BasePydanticVectorStore, VectorStoreQuery, VectorStoreQueryResult
from llama_index.core.schema import TextNode
from sqlalchemy import text

from app.database import SessionLocal
from app.services.embedding import embed_texts
from app.services.reranker import rerank
from app.utils.logger import get_logger

logger = get_logger("agent_retriever")

SCORE_THRESHOLD = 0.4  # 与现有系统一致
RETRIEVE_TOP_K = 10  # LlamaIndex 召回条数
RERANK_TOP_K = 5  # 重排后条数

# 检索权限上下文：chat 请求内设置，检索 SQL 据此过滤
_retrieve_scope: ContextVar[dict] = ContextVar("retrieve_scope", default={"user_id": None, "role_level": 10})


def set_retrieve_scope(user_id: str | None, role_level: int = 10) -> None:
    """在 chat 请求入口设置检索权限 scope（FastAPI 每请求协程隔离）"""
    _retrieve_scope.set({"user_id": user_id, "role_level": role_level or 10})


def _perm_filter_sql() -> str:
    """拼权限过滤 SQL：文档级（本人 或 共享达标）+ 片段级（sensitivity/min_level）"""
    scope = _retrieve_scope.get()
    uid = scope.get("user_id")
    doc_cond = (
        "(d.user_id = :perm_uid OR (d.visibility <> 'restricted' AND d.min_level <= :perm_lv))"
        if uid
        else "(d.visibility <> 'restricted' AND d.min_level <= :perm_lv)"
    )
    return f"""AND ({doc_cond})
               AND (c.sensitivity IS NULL OR c.sensitivity = 'public' OR c.min_level <= :perm_lv)"""


def _perm_params() -> dict:
    scope = _retrieve_scope.get()
    return {"perm_uid": scope.get("user_id"), "perm_lv": scope.get("role_level") or 10}


class BgeM3Embedding(BaseEmbedding):
    """LlamaIndex Embedding 适配器：复用现有 bge-m3 单例"""

    def _get_query_embedding(self, query: str) -> list[float]:
        return embed_texts([query])[0]

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return embed_texts([text])[0]

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts)

    async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._get_text_embeddings(texts)


class ChunksVectorStore(BasePydanticVectorStore):
    """只读挂载现有 chunks 表（pgvector 余弦检索）"""

    stores_text: bool = True

    def query(self, query: VectorStoreQuery, **kwargs) -> VectorStoreQueryResult:
        q_vec = query.query_embedding or embed_texts([query.query_str])[0]
        db = SessionLocal()
        try:
            sql = f"""SELECT c.id, c.parent_chunk_id, c.doc_id, c.content, d.title
                     FROM chunks c
                     JOIN documents d ON c.doc_id = d.id
                     WHERE c.is_deleted = FALSE
                     {_perm_filter_sql()}
                     ORDER BY c.embedding <=> :vec LIMIT :k"""
            params = {**_perm_params(), "vec": str(q_vec), "k": query.similarity_top_k}
            rows = db.execute(text(sql), params).fetchall()
        finally:
            db.close()
        nodes = [
            TextNode(
                text=r[3],
                id_=str(r[0]),
                metadata={
                    "doc_id": str(r[2]),
                    "parent_chunk_id": str(r[1]) if r[1] else None,
                    "title": r[4],
                },
            )
            for r in rows
        ]
        ids = [str(r[0]) for r in rows]
        return VectorStoreQueryResult(nodes=nodes, similarities=[0.0] * len(rows), ids=ids)

    def add(self, nodes, **kwargs):
        raise NotImplementedError("chunks 表由文档上传链路写入，本 store 只读")

    def delete(self, ref_doc_id, **kwargs):
        raise NotImplementedError("chunks 表由文档上传链路写入，本 store 只读")

    @property
    def client(self):
        return None


_retriever: Optional[BaseRetriever] = None
_retriever_lock = threading.Lock()


def get_llama_retriever() -> BaseRetriever:
    """单例：LlamaIndex 检索器（复用现有向量数据）"""
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                store = ChunksVectorStore()
                index = VectorStoreIndex.from_vector_store(store, embed_model=BgeM3Embedding())
                _retriever = index.as_retriever(similarity_top_k=RETRIEVE_TOP_K)
                logger.info("LlamaIndex 检索器初始化完成")
    return _retriever


def _fetch_parent_contents(rows: list[dict]) -> list[dict]:
    """父子块回表：命中子块时用父块内容替换（与现有逻辑一致）"""
    parent_ids = list({r["parent_chunk_id"] for r in rows if r.get("parent_chunk_id")})
    if not parent_ids:
        return rows
    db = SessionLocal()
    try:
        parents = db.execute(
            text("SELECT id, content FROM chunks WHERE id = ANY(:ids)"),
            {"ids": parent_ids},
        ).fetchall()
        parent_map = {str(r[0]): r[1] for r in parents}
        result = []
        for r in rows:
            pid = r.get("parent_chunk_id")
            if pid and pid in parent_map:
                result.append({**r, "content": parent_map[pid][:1000]})
            else:
                result.append(r)
        return result
    finally:
        db.close()


_REWRITE_PROMPT = """你是检索查询改写器。用户的问题将用于向量检索，请把它改写成"检索友好"的查询：
1. 保留核心实体/术语（如 API、X-API-Key、退货政策、考勤、鉴权）
2. 补充文档中可能出现的同义词、专有名词、完整写法（例如"接口鉴权"改写为"接口鉴权 X-API-Key 请求头 Authorization 认证方式"）
3. 去掉口语、疑问语气、代词，只保留关键词
4. 只输出一行改写后的查询，不要解释、不要加引号"""

_REWRITE_SKIP_RE = None  # 预留


def _rewrite_query(question: str) -> str:
    """LLM 查询改写：把口语/术语 query 转成检索友好的关键词组合（弥补"接口鉴权"→X-API-Key 类缺口）
    - 开关：system_configs.query_rewrite_enabled（默认开，管理界面可热关）
    - 降级：短 query / 异常 / 超时 → 返回原 query，绝不阻断检索
    """
    q = (question or "").strip()
    if not q or len(q) < 4:
        return q  # 3 字以内不值得改写（4 字如"接口鉴权"仍需改写补术语）
    from app.services.system_configs import get_config_bool

    if not get_config_bool("query_rewrite_enabled", True):
        return q
    try:
        from langchain_openai import ChatOpenAI
        from app.config import settings

        client = ChatOpenAI(
            model=getattr(settings, "PREFILTER_LIGHT_MODEL", "qwen-turbo"),
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=8,
            temperature=0,
        )
        resp = client.invoke(
            [
                {"role": "system", "content": _REWRITE_PROMPT},
                {"role": "user", "content": q},
            ]
        )
        rewritten = (resp.content or "").strip().strip('"').strip("“”")
        if not rewritten:
            return q
        logger.info(f"查询改写: {q[:40]} → {rewritten[:60]}")
        return rewritten[:200]
    except Exception as e:
        logger.warning(f"查询改写失败，降级用原 query: {str(e)[:120]}")
        return q


def rag_retrieve(state) -> dict:
    """检索节点：LlamaIndex 召回 → 父子回表 → 重排 → 阈值过滤，StreamWriter 推送引用元数据"""
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()
    question = state.get("question", "")

    # 权限注入：从 AgentState 取 user_id → 查 role_level → 设置检索 scope
    uid = state.get("user_id")
    if uid:
        lv = 10
        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT role_level FROM users WHERE id = :uid AND is_deleted = FALSE"), {"uid": uid}
            ).fetchone()
            lv = row[0] if row else 10
        except Exception as e:
            logger.warning(f"读取用户权限等级失败，默认 10: {e}")
        finally:
            db.close()
        set_retrieve_scope(uid, lv)

    # 查询改写：召回用改写后的检索词（弥补术语鸿沟），失败降级原 query
    retrieve_query = _rewrite_query(question)
    retriever = get_llama_retriever()
    nodes = retriever.retrieve(retrieve_query)

    rows = [
        {
            "chunk_id": n.node.node_id,
            "parent_chunk_id": n.node.metadata.get("parent_chunk_id"),
            "doc_id": n.node.metadata.get("doc_id"),
            "content": n.node.text,
            "doc_title": n.node.metadata.get("title", "未知文档"),
        }
        for n in nodes
    ]
    logger.info(f"LlamaIndex 召回 {len(rows)} 条（改写后 query: {retrieve_query[:50]}）")

    # 父子块回表（质量与现有系统一致）
    rows = _fetch_parent_contents(rows)

    # bge-reranker 重排（本地 CrossEncoder，与现有系统一致）
    docs = [r["content"] for r in rows]
    reranked = rerank(retrieve_query, docs, top_k=RERANK_TOP_K)  # 重排与召回口径一致（改写 query）
    reranked_with_meta = []
    for doc, score in reranked:
        for r in rows:
            if r["content"] == doc:
                reranked_with_meta.append(
                    {
                        "content": doc,
                        "doc_title": r.get("doc_title", "未知文档"),
                        "score": float(score),
                    }
                )
                break

    # 阈值过滤
    filtered = [item for item in reranked_with_meta if item["score"] >= SCORE_THRESHOLD]
    logger.info(f"重排 {len(reranked_with_meta)} 条，阈值 {SCORE_THRESHOLD} 过滤后 {len(filtered)} 条")

    contexts = [item["content"] for item in filtered]
    contexts_meta = [
        {
            "index": i + 1,
            "title": item["doc_title"],
            "content": item["content"][:150] + "...",
        }
        for i, item in enumerate(filtered)
    ]
    from app.agent.tracing import emit as _trace_emit

    _trace_emit(
        state,
        "retrieve",
        {
            "query": retrieve_query,
            "recall": len(rows),
            "reranked": len(reranked_with_meta),
            "filtered": len(filtered),
            "titles": [m["title"] for m in contexts_meta],
        },
    )
    result = {"contexts": contexts, "contexts_meta": contexts_meta}
    try:
        writer({"type": "contexts", **result})
    except Exception:
        pass  # 无流式消费者时忽略
    return result
