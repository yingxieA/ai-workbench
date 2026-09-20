"""Embedding 服务 - bge-m3 本地 GPU 推理"""
import os
import time
import threading
from sentence_transformers import SentenceTransformer
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("embedding")
_model = None
_model_lock = threading.Lock()


def _resolve_model_path():
    path = settings.EMBEDDING_MODEL
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", path))


def get_embedding_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                model_path = _resolve_model_path()
                logger.info(f"加载模型: {model_path}, device={settings.EMBEDDING_DEVICE}")
                import torch
                logger.info(f"CUDA 可用: {torch.cuda.is_available()}")
                if torch.cuda.is_available():
                    logger.info(f"GPU 显存: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
                t0 = time.time()
                _model = SentenceTransformer(model_path, device=settings.EMBEDDING_DEVICE)
                logger.info(f"模型加载完成，耗时 {time.time()-t0:.1f}s")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化，返回 1024 维向量"""
    model = get_embedding_model()
    logger.info(f"向量化 {len(texts)} 个块")
    t0 = time.time()
    embeddings = model.encode(texts, normalize_embeddings=True)
    logger.info(f"向量化完成，耗时 {time.time()-t0:.2f}s")
    return embeddings.tolist()
