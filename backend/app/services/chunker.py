"""文本分片服务"""

import re

import jieba

from app.services.system_configs import get_config


def _get_int(key: str, default: int) -> int:
    """从 system_configs 读正整数配置，解析失败回退默认"""
    raw = get_config(key, "")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except Exception:
            pass
    return default


def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """按段落分片；chunk_size/overlap 默认读配置 rag_chunk_size / rag_chunk_overlap"""
    if chunk_size is None:
        chunk_size = _get_int("rag_chunk_size", 512)
    if overlap is None:
        overlap = _get_int("rag_chunk_overlap", 64)
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) <= chunk_size:
            current += para + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
            if len(para) > chunk_size:
                sentences = re.split(r"[。！？]", para)
                buf = ""
                for s in sentences:
                    # 无标点超长段：按 chunk_size 硬切，防止单块无限长（如 base64/长 URL）
                    while len(s) > chunk_size:
                        if buf:
                            chunks.append(buf)
                            buf = ""
                        chunks.append(s[:chunk_size])
                        s = s[chunk_size:]
                    if buf and len(buf) + len(s) > chunk_size:
                        chunks.append(buf)
                        buf = ""
                    buf += s + "。"
                if buf:
                    current = buf
            else:
                current = para

    if current:
        chunks.append(current.strip())

    return chunks


def build_tsv(text: str) -> str:
    """用 jieba 分词生成 PG tsvector 格式"""
    words = jieba.cut(text)
    return " ".join(words)
