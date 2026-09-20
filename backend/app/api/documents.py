"""文档上传 API"""
import hashlib
import io
import os
from fastapi import APIRouter, UploadFile, File, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.models.document import Document, Chunk
from app.services.chunker import split_text, build_tsv
from app.services.embedding import embed_texts
from app.services.parser import parse_pdf, parse_ipynb
from app.utils.logger import get_logger
from minio import Minio

logger = get_logger("documents")

router = APIRouter(prefix="/api/documents", tags=["documents"])

minio_client = Minio("localhost:9000", access_key="admin", secret_key="admin123", secure=False)
BUCKET = "documents"
if not minio_client.bucket_exists(BUCKET):
    minio_client.make_bucket(BUCKET)


@router.get("/list")
def list_documents(skip: int = 0, limit: int = 10, type: str = None, db: Session = Depends(get_db)):
    """列出所有文档"""
    q = db.query(Document).filter(Document.is_deleted == False)
    if type:
        q = q.filter(Document.source == type)
    total = q.count()
    docs = q.order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
    items = []
    for d in docs:
        chunk_count = db.query(Chunk).filter(Chunk.doc_id == d.id).count()
        items.append({
            "id": str(d.id),
            "title": d.title,
            "category": d.category,
            "source": d.source,
            "chunk_count": chunk_count,
            "uploaded_at": d.created_at.isoformat() if d.created_at else None
        })
    return {"total": total, "items": items}


@router.delete("/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    """删除文档"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        logger.info(f"删除文档: {doc.title}")
        doc.is_deleted = True
        from datetime import datetime
        doc.deleted_at = datetime.now()
        db.query(Chunk).filter(Chunk.doc_id == doc_id).update({'is_deleted': True})
        db.commit()
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str


@router.get("/{doc_id}/preview")
def preview_document(doc_id: str, db: Session = Depends(get_db)):
    """在线预览 PDF"""
    from urllib.parse import quote
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.file_path:
        logger.error(f"预览文件不存在: {doc_id}")
        return {"error": "文件不存在"}
    logger.info(f"预览文档: {doc.title}")
    try:
        resp = minio_client.get_object(BUCKET, doc.file_path)
        data = resp.read()
        logger.info(f"文件大小: {len(data)} bytes")
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.title)}"}
        )
    finally:
        resp.close()
        resp.release_conn()


@router.get("/{doc_id}/download")
def download_document(doc_id: str, db: Session = Depends(get_db)):
    """下载文档"""
    from urllib.parse import quote
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.file_path:
        logger.error(f"文件不存在: {doc_id}")
        return {"error": "文件不存在"}
    logger.info(f"下载文档: {doc.title}, file_path={doc.file_path}, is_pdf={doc.title.lower().endswith('.pdf')}")
    try:
        resp = minio_client.get_object(BUCKET, doc.file_path)
        data = resp.read()
        logger.info(f"文件大小: {len(data)} bytes")
        filename = quote(doc.title)
        is_pdf = doc.title.lower().endswith('.pdf')
        return Response(
            content=data,
            media_type="application/pdf" if is_pdf else "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
        )
    except Exception as e:
        logger.error(f"下载失败: {e}")
        raise
    finally:
        resp.close()
        resp.release_conn()


@router.put("/{doc_id}")
def rename_document(doc_id: str, req: RenameRequest, db: Session = Depends(get_db)):
    """重命名文档"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        doc.title = req.title
        db.commit()
    return {"ok": True}


@router.post("/upload-async")
async def upload_async(
    file: UploadFile = File(...),
    category: str = None,
    db: Session = Depends(get_db)
):
    """异步上传 PDF"""
    import uuid as _uuid
    task_id = str(_uuid.uuid4())
    content = await file.read()
    logger.info(f"上传文件: {file.filename}, 大小: {len(content)} bytes, task_id={task_id}")
    filename = file.filename

    # 文件校验
    ALLOWED_EXT = {'.pdf', '.md', '.txt', '.ipynb'}
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return {"status": "error", "error": f"不支持的文件类型: {ext}"}
    if len(content) > 20 * 1024 * 1024:
        return {"status": "error", "error": "文件超过 20MB"}
    db.execute(text("INSERT INTO pdf_tasks (id, filename, status) VALUES (:id, :fn, 'pending')"), {"id": task_id, "fn": filename})
    db.commit()
    # 后台任务
    import threading
    def bg_task():
        import asyncio
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        from app.services.chunker import split_text, build_tsv
        from app.services.embedding import embed_texts
        from app.services.parser import parse_pdf
        db2 = SessionLocal()
        try:
            db2.execute(_text("UPDATE pdf_tasks SET status='processing' WHERE id=:id"), {"id": task_id})
            db2.commit()
            parsed = asyncio.run(parse_pdf(content, filename))
            print(f"[PDF任务] parse_pdf 类型: {type(parsed)}")
            print(f"[PDF任务] parse_pdf 前200字: {str(parsed)[:200]}")
            if not isinstance(parsed, str):
                parsed = str(parsed)
            pieces = split_text(parsed)
            print(f"[PDF任务] 分片数: {len(pieces)}")
            chunk_data = [{"text": p, "chunk_type": "text", "source_type": "pdf"} for p in pieces]
            texts = [c["text"] for c in chunk_data]
            embeddings = embed_texts(texts)
            md5 = hashlib.md5(content).hexdigest()
            object_name = f"{md5}/{filename}"
            minio_client.put_object(BUCKET, object_name, io.BytesIO(content), len(content), content_type="application/pdf")
            doc = Document(title=filename, category=category, source="pdf", md5_hash=md5, file_path=object_name, model_version="bge-m3-v1")
            db2.add(doc)
            db2.flush()
            for i, (c, emb) in enumerate(zip(chunk_data, embeddings)):
                db2.add(Chunk(doc_id=doc.id, chunk_index=i, chunk_type=c["chunk_type"], source_type=c["source_type"], content=c["text"], embedding=emb, tsv=build_tsv(c["text"]), model_version="bge-m3-v1"))
            db2.commit()
            db2.execute(_text("UPDATE pdf_tasks SET status='done', chunks=:n WHERE id=:id"), {"n": len(chunk_data), "id": task_id})
            db2.commit()
        except Exception as e:
            db2.rollback()
            db2.execute(_text("UPDATE pdf_tasks SET status='failed', error=:e WHERE id=:id"), {"e": str(e), "id": task_id})
            db2.commit()
        finally:
            db2.close()
    threading.Thread(target=bg_task, daemon=True).start()
    return {"task_id": task_id, "status": "pending"}


@router.get("/task/{task_id}")
def task_status(task_id: str, db: Session = Depends(get_db)):
    """查询任务状态"""
    row = db.execute(text("SELECT status, chunks, error FROM pdf_tasks WHERE id=:id"), {"id": task_id}).fetchone()
    if not row:
        return {"status": "not_found"}
    return {"status": row[0], "chunks": row[1], "error": row[2]}


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    category: str = None,
    db: Session = Depends(get_db)
):
    content = await file.read()
    filename = file.filename

    # MD5 去重
    md5 = hashlib.md5(content).hexdigest()
    exists = db.query(Document).filter(Document.md5_hash == md5).first()
    if exists:
        return {"status": "exists", "doc_id": str(exists.id)}

    # 按文件类型解析
    if filename.endswith(".pdf"):
        text = await parse_pdf(content, filename)
        pieces = split_text(text)
        chunk_data = [{"text": p, "chunk_type": "text", "source_type": "pdf"} for p in pieces]
        source = "pdf"
    elif filename.endswith(".ipynb"):
        cells = parse_ipynb(content)
        chunk_data = []
        for c in cells:
            for piece in split_text(c["content"]):
                chunk_data.append({
                    "text": piece,
                    "chunk_type": c["type"],
                    "source_type": c["source_type"]
                })
        source = "jupyter"
    else:
        text = content.decode("utf-8", errors="ignore")
        pieces = split_text(text)
        chunk_data = [{"text": p, "chunk_type": "text", "source_type": "markdown"} for p in pieces]
        source = "markdown" if filename.endswith(".md") else "text"

    if not chunk_data:
        return {"status": "empty"}

    # 事务外算 embedding
    texts = [c["text"] for c in chunk_data]
    embeddings = embed_texts(texts)

    # 上传到 MinIO
    object_name = f"{md5}/{filename}"
    minio_client.put_object(
        BUCKET, object_name,
        io.BytesIO(content), len(content),
        content_type=file.content_type or "application/octet-stream"
    )

    try:
        doc = Document(
            title=filename,
            category=category,
            source=source,
            md5_hash=md5,
            file_path=object_name,
            model_version="bge-m3-v1"
        )
        db.add(doc)
        db.flush()

        chunk_objects = []
        for i, (c, emb) in enumerate(zip(chunk_data, embeddings)):
            chunk_objects.append(Chunk(
                doc_id=doc.id,
                chunk_index=i,
                chunk_type=c["chunk_type"],
                source_type=c["source_type"],
                content=c["text"],
                embedding=emb,
                tsv=build_tsv(c["text"]),
                model_version="bge-m3-v1"
            ))

        db.add_all(chunk_objects)
        db.commit()
        return {"status": "ok", "doc_id": str(doc.id), "chunks": len(chunk_data)}
    except Exception as e:
        db.rollback()
        raise e

