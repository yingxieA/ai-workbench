"""Reranker 服务 - bge-reranker-v2-m3 本地 GPU 推理"""

import os
from sentence_transformers import CrossEncoder
from app.config import settings

_model = None


def _resolve_model_path():
    path = settings.RERANKER_MODEL
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", path))


def get_reranker():
    global _model
    if _model is None:
        model_path = _resolve_model_path()
        print(f"加载 Reranker: {model_path}")
        _model = CrossEncoder(model_path, device=settings.RERANKER_DEVICE)
        print("Reranker 加载完成")
    return _model


def rerank(query: str, documents: list[str], top_k: int = 5) -> list[tuple[str, float]]:
    """重排：返回 [(doc, score), ...] 按分数降序"""
    model = get_reranker()
    pairs = [(query, doc) for doc in documents]
    scores = model.predict(pairs)
    ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]
