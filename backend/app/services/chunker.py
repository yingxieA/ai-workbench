"""文本分片服务"""

import re
import jieba


def split_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """按段落分片，带重叠"""
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
                    if len(buf) + len(s) <= chunk_size:
                        buf += s + "。"
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = s + "。"
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
