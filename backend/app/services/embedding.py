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
                logger.info(f"模型加载完成，耗时 {time.time() - t0:.1f}s")
    return _model


def embed_texts(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    """分批向量化，返回 1024 维向量；CUDA OOM 时降级小 batch 重试"""
    import numpy as np
    import torch

    model = get_embedding_model()
    logger.info(f"向量化 {len(texts)} 个块, batch_size={batch_size}")
    t0 = time.time()
    outs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            outs.append(model.encode(batch, normalize_embeddings=True))
        except torch.cuda.OutOfMemoryError:
            logger.warning(f"向量化 OOM @ batch {i}, 降级 batch_size=4 重试")
            sub = []
            for j in range(0, len(batch), 4):
                try:
                    sub.append(model.encode(batch[j : j + 4], normalize_embeddings=True))
                except torch.cuda.OutOfMemoryError:
                    # 单条重试，再失败则抛错（由调用方处理）
                    sub.append(model.encode(batch[j : j + 1], normalize_embeddings=True))
            outs.append(np.concatenate(sub, axis=0))
    embeddings = np.concatenate(outs, axis=0)
    logger.info(f"向量化完成，耗时 {time.time() - t0:.2f}s")
    return embeddings.tolist()
