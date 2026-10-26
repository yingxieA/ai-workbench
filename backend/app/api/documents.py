"""文档上传 API：admin 内容生产 + 文档级/片段级权限控制"""

from app.core.errors import ErrorCode
import hashlib
import io
import json
import os
import re
import uuid as _uuid
from datetime import timedelta
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text, or_, and_
from app.database import get_db
from app.models.document import Document, Chunk, AuditLog
from app.models.user import User
from app.services.chunker import split_text, build_tsv
from app.services.embedding import embed_texts
from app.services.parser import parse_pdf, parse_ipynb
from app.services.sensitive_classifier import label_chunks_batch
from app.api.auth import get_current_user, require_admin
from app.utils.logger import get_logger
from minio import Minio

logger = get_logger("documents")

router = APIRouter(prefix="/api/documents", tags=["documents"])

minio_client = Minio("localhost:9000", access_key="admin", secret_key="admin123", secure=False)
BUCKET = "documents"
if not minio_client.bucket_exists(BUCKET):
    minio_client.make_bucket(BUCKET)


def _audit(db: Session, user_id: str, action: str, target_id: str = None, detail: dict = None):
    """审计落库（独立 commit，不依赖调用点后续操作；失败不影响主流程）"""
    try:
        db.add(AuditLog(user_id=user_id, action=action, target_id=target_id, detail=detail))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"写审计日志失败: {e}")


def _owned_doc_query(db: Session, user_id: str, doc_id: str) -> Document | None:
    """admin 可操作全部文档；非 admin 仅本人文档（管理操作校验）"""
    doc = db.query(Document).filter(Document.is_deleted == False, Document.id == doc_id).first()
    if not doc:
        return None
    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    is_admin = user and (user.role_level or 10) >= 100
    if not is_admin and doc.user_id != user_id:
        return None
    return doc


def _visible_doc_query(db: Session, user_id: str, role_level: int, doc_id: str) -> Document | None:
    """可见文档校验：本人文档 或 共享且等级达标（浏览/预览/下载用）"""
    doc = db.query(Document).filter(Document.is_deleted == False, Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_NOT_FOUND, "文档不存在"))
    if doc.user_id == user_id:
        return doc
    if doc.visibility != "restricted" and (doc.min_level or 10) <= (role_level or 10):
        return doc
    raise HTTPException(status_code=403, detail=(ErrorCode.DOC_ACCESS_DENIED, "无权访问该文档"))


@router.get("/list")
def list_documents(
    skip: int = 0,
    limit: int = 10,
    type: str = None,
    search: str = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户可见的文档：本人文档 + 共享且等级达标的文档"""
    user_id, role_level = user.id, user.role_level or 10
    q = db.query(Document).filter(
        Document.is_deleted == False,
        or_(
            Document.user_id == user_id,
            and_(
                Document.visibility != "restricted",
                Document.min_level <= role_level,
            ),
        ),
    )
    if type:
        q = q.filter(Document.source == type)
    if search:
        q = q.filter(Document.title.ilike(f"%{search}%"))
    total = q.count()
    docs = q.order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
    items = []
    for d in docs:
        chunk_count = db.query(Chunk).filter(Chunk.doc_id == d.id).count()
        items.append(
            {
                "id": str(d.id),
                "title": d.title,
                "category": d.category,
                "source": d.source,
                "chunk_count": chunk_count,
                "visibility": d.visibility or "internal",
                "min_level": d.min_level or 10,
                "owner": d.user_id,
                "is_owner": d.user_id == user_id,
                "uploaded_at": d.created_at.isoformat() if d.created_at else None,
            }
        )
    return {"total": total, "items": items}


@router.delete("/{doc_id}")
def delete_document(doc_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除文档（admin 全部；非 admin 仅本人）"""
    doc = _owned_doc_query(db, user.id, doc_id)
    if doc:
        logger.info(f"删除文档: {doc.title}")
        doc.is_deleted = True
        from datetime import datetime

        doc.deleted_at = datetime.now()
        db.query(Chunk).filter(Chunk.doc_id == doc_id).update({"is_deleted": True})
        _audit(db, user.id, "delete_document", str(doc.id), {"title": doc.title})
        db.commit()
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str


@router.get("/{doc_id}/raw-content")
def get_raw_content(doc_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取文档原始内容（在线预览用，对可见用户开放；不存在 404 / 无权 403）"""
    from urllib.parse import quote

    doc = _visible_doc_query(db, user.id, user.role_level or 10, doc_id)
    if not doc or not doc.file_path:
        logger.error(f"原始内容接口：文件不存在: {doc_id}")
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_FILE_NOT_FOUND, "文件不存在"))

    logger.info(f"获取原始内容: {doc.title}, file_path={doc.file_path}")

    try:
        resp = minio_client.get_object(BUCKET, doc.file_path)
        data = resp.read()
        logger.info(f"文件大小: {len(data)} bytes")

        title_lower = doc.title.lower()

        # PDF 文件返回二进制
        if title_lower.endswith(".pdf"):
            return Response(
                content=data,
                media_type="application/pdf",
                headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.title)}"},
            )

        # 文本文件（md, txt, ipynb）返回文本
        try:
            text = data.decode("utf-8")
            return Response(
                content=text,
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.title)}"},
            )
        except UnicodeDecodeError:
            return Response(content=data, media_type="application/octet-stream")

    except Exception as e:
        logger.error(f"获取原始内容失败: {e}")
        return {"error": f"获取内容失败: {str(e)}"}
    finally:
        try:
            resp.close()
            resp.release_conn()
        except Exception:
            pass


@router.get("/{doc_id}/preview")
def preview_document(doc_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """在线预览 PDF（对可见用户开放；不存在 404 / 无权 403）"""
    from urllib.parse import quote

    doc = _visible_doc_query(db, user.id, user.role_level or 10, doc_id)
    if not doc or not doc.file_path:
        logger.error(f"预览文件不存在: {doc_id}")
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_FILE_NOT_FOUND, "文件不存在"))
    logger.info(f"预览文档: {doc.title}")
    try:
        resp = minio_client.get_object(BUCKET, doc.file_path)
        data = resp.read()
        logger.info(f"文件大小: {len(data)} bytes")
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.title)}"},
        )
    finally:
        resp.close()
        resp.release_conn()


@router.get("/{doc_id}/download")
def download_document(doc_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """下载文档（对可见用户开放；不存在 404 / 无权 403）"""
    from urllib.parse import quote

    doc = _visible_doc_query(db, user.id, user.role_level or 10, doc_id)
    if not doc or not doc.file_path:
        logger.error(f"文件不存在: {doc_id}")
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_FILE_NOT_FOUND, "文件不存在"))
    logger.info(f"下载文档: {doc.title}, file_path={doc.file_path}, is_pdf={doc.title.lower().endswith('.pdf')}")
    try:
        resp = minio_client.get_object(BUCKET, doc.file_path)
        data = resp.read()
        logger.info(f"文件大小: {len(data)} bytes")
        filename = quote(doc.title)
        is_pdf = doc.title.lower().endswith(".pdf")
        return Response(
            content=data,
            media_type="application/pdf" if is_pdf else "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
    except Exception as e:
        logger.error(f"下载失败: {e}")
        raise
    finally:
        resp.close()
        resp.release_conn()


@router.put("/{doc_id}")
def rename_document(
    doc_id: str,
    req: RenameRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重命名文档（admin 全部；非 admin 仅本人）"""
    doc = _owned_doc_query(db, user.id, doc_id)
    if doc:
        doc.title = req.title
        db.commit()
        _audit(db, user.id, "rename_document", str(doc.id), {"title": req.title})
    return {"ok": True}


class PermissionRequest(BaseModel):
    visibility: str = "internal"  # public / internal / restricted
    min_level: int = 10


class ChunkLabelRequest(BaseModel):
    sensitivity: str = "public"  # public / internal / secret
    min_level: int = 10
    reason: str = ""


@router.patch("/{doc_id}/permission")
def set_document_permission(
    doc_id: str,
    req: PermissionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """设置文档级权限（仅 admin）"""
    doc = _owned_doc_query(db, admin.id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_NOT_FOUND, "文档不存在"))
    if req.visibility not in ("public", "internal", "restricted"):
        raise HTTPException(
            status_code=400, detail=(ErrorCode.DOC_VISIBILITY_INVALID, "visibility 仅支持 public/internal/restricted")
        )
    doc.visibility = req.visibility
    doc.min_level = max(10, min(100, req.min_level))
    _audit(
        db,
        admin.id,
        "set_permission",
        str(doc.id),
        {"title": doc.title, "visibility": req.visibility, "min_level": req.min_level},
    )
    db.commit()
    return {"ok": True, "visibility": doc.visibility, "min_level": doc.min_level}


@router.get("/{doc_id}/chunks")
def list_doc_chunks(
    doc_id: str,
    skip: int = 0,
    limit: int = 50,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """复核列表：查看文档全部片段的敏感标记（仅 admin）"""
    doc = db.query(Document).filter(Document.id == doc_id, Document.is_deleted == False).first()
    if not doc:
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_NOT_FOUND, "文档不存在"))
    q = db.query(Chunk).filter(Chunk.doc_id == doc_id, Chunk.is_deleted == False)
    total = q.count()
    rows = q.order_by(Chunk.chunk_index.asc()).offset(skip).limit(limit).all()
    items = [
        {
            "id": str(c.id),
            "chunk_index": c.chunk_index,
            "chunk_type": c.chunk_type,
            "content": c.content[:500],
            "sensitivity": c.sensitivity or "public",
            "min_level": c.min_level or 10,
            "classify_reason": c.classify_reason,
        }
        for c in rows
    ]
    return {
        "total": total,
        "items": items,
        "doc": {"id": str(doc.id), "title": doc.title},
    }


@router.patch("/{doc_id}/chunks/{chunk_id}")
def update_chunk_label(
    doc_id: str,
    chunk_id: str,
    req: ChunkLabelRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """人工复核：修改片段敏感标记（仅 admin）"""
    if req.sensitivity not in ("public", "internal", "secret"):
        raise HTTPException(
            status_code=400, detail=(ErrorCode.DOC_SENSITIVITY_INVALID, "sensitivity 仅支持 public/internal/secret")
        )
    chunk = db.query(Chunk).filter(Chunk.id == chunk_id, Chunk.doc_id == doc_id, Chunk.is_deleted == False).first()
    if not chunk:
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_CHUNK_NOT_FOUND, "片段不存在"))
    chunk.sensitivity = req.sensitivity
    chunk.min_level = max(10, min(100, req.min_level))
    chunk.classify_reason = req.reason or f"人工复核修改为 {req.sensitivity}"
    _audit(
        db,
        admin.id,
        "review_chunk",
        str(chunk.id),
        {
            "doc_id": doc_id,
            "sensitivity": req.sensitivity,
            "min_level": req.min_level,
            "reason": req.reason,
        },
    )
    db.commit()
    return {"ok": True}


@router.post("/{doc_id}/relabel")
def relabel_document(doc_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """重新打标整个文档（admin 手动触发，LLM 兜底重新分类）"""
    doc = db.query(Document).filter(Document.id == doc_id, Document.is_deleted == False).first()
    if not doc:
        raise HTTPException(status_code=404, detail=(ErrorCode.DOC_NOT_FOUND, "文档不存在"))
    chunks = db.query(Chunk).filter(Chunk.doc_id == doc_id, Chunk.is_deleted == False).all()
    texts = [c.content for c in chunks]
    labels = label_chunks_batch(texts)
    for c, lab in zip(chunks, labels):
        c.sensitivity = lab["sensitivity"]
        c.min_level = lab["min_level"]
        c.classify_reason = lab["reason"]
    _audit(db, admin.id, "relabel_document", str(doc.id), {"chunks": len(chunks)})
    db.commit()
    return {"ok": True, "relabeled": len(chunks)}


@router.post("/upload-async")
async def upload_async(
    file: UploadFile = File(...),
    category: str = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """异步上传 PDF（仅 admin 内容生产）"""
    user_id = admin.id
    task_id = str(_uuid.uuid4())
    content = await file.read()
    logger.info(f"上传文件: {file.filename}, 大小: {len(content)} bytes, task_id={task_id}, user={user_id}")
    filename = file.filename

    # 文件校验
    ALLOWED_EXT = {".pdf", ".md", ".txt", ".ipynb"}
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return {"status": "error", "error": f"不支持的文件类型: {ext}"}
    if len(content) > 20 * 1024 * 1024:
        return {"status": "error", "error": "文件超过 20MB"}
    db.execute(
        text("INSERT INTO pdf_tasks (id, filename, status) VALUES (:id, :fn, 'pending')"),
        {"id": task_id, "fn": filename},
    )
    db.commit()
    # 后台任务

    def bg_task():
        import asyncio
        import re
        from datetime import timedelta
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        from app.services.chunker import split_text, build_tsv
        from app.services.embedding import embed_texts
        from app.services.parser import parse_pdf
        from app.services.vision import describe_image

        db2 = SessionLocal()
        try:
            db2.execute(
                _text("UPDATE pdf_tasks SET status='processing' WHERE id=:id"),
                {"id": task_id},
            )
            db2.commit()
            parsed, images = asyncio.run(parse_pdf(content, filename))
            print(f"[PDF任务] parse_pdf 类型: {type(parsed)}, 图片数: {len(images)}")
            print(f"[PDF任务] parse_pdf 前200字: {str(parsed)[:200]}")
            if not isinstance(parsed, str):
                parsed = str(parsed)
            md5 = hashlib.md5(content).hexdigest()
            object_name = f"{md5}/{filename}"
            minio_client.put_object(
                BUCKET,
                object_name,
                io.BytesIO(content),
                len(content),
                content_type="application/pdf",
            )
            # 图片上传 MinIO + md 引用替换为 presigned URL（P0 多模态）
            image_urls: dict[str, str] = {}
            for img in images:
                base = img["ref_name"].replace("\\", "/").split("/")[-1]
                img_obj = f"images/{md5}/{base}"
                minio_client.put_object(
                    BUCKET,
                    img_obj,
                    io.BytesIO(img["data"]),
                    len(img["data"]),
                    content_type=img["mime"],
                )
                url = minio_client.presigned_get_object(BUCKET, img_obj, expires=timedelta(days=7))
                image_urls[img["ref_name"].replace("\\", "/")] = url
                logger.info(f"图片入库: {base} -> {url[:80]}...")
            for ref, url in image_urls.items():
                parsed = re.sub(rf"!\[[^\]]*\]\(\s*{re.escape(ref)}\s*\)", f"![图片]({url})", parsed)

            pieces = split_text(parsed)
            print(f"[PDF任务] 分片数: {len(pieces)}")
            chunk_data = [{"text": p, "chunk_type": "text", "source_type": "pdf"} for p in pieces]
            # 图片块（P1 图注索引）：每张图片生成视觉描述 → image_caption 块
            for img in images:
                ref = img["ref_name"].replace("\\", "/")
                base = ref.split("/")[-1]
                caption = describe_image(img["data"], img["mime"])
                if caption:
                    text = caption
                else:
                    text = f"[图片图表] {base}（视觉描述未生成，可查看原图）"
                    logger.warning(f"图注生成失败，写入占位块: {base}")
                chunk_data.append(
                    {
                        "text": text,
                        "chunk_type": "image_caption",
                        "source_type": "pdf",
                        "resource_url": image_urls.get(ref),
                    }
                )
            texts = [c["text"] for c in chunk_data]
            embeddings = embed_texts(texts)
            doc = Document(
                title=filename,
                category=category,
                source="pdf",
                md5_hash=md5,
                file_path=object_name,
                model_version="bge-m3-v1",
                user_id=user_id,
                visibility="internal",
                min_level=10,
            )
            db2.add(doc)
            db2.flush()
            # 敏感片段自动打标（标记语法 → 关键词 → LLM 兜底）
            labels = label_chunks_batch([c["text"] for c in chunk_data])
            for i, (c, emb) in enumerate(zip(chunk_data, embeddings)):
                lab = labels[i]
                db2.add(
                    Chunk(
                        doc_id=doc.id,
                        chunk_index=i,
                        chunk_type=c["chunk_type"],
                        source_type=c["source_type"],
                        content=c["text"],
                        resource_url=c.get("resource_url"),
                        embedding=emb,
                        tsv=build_tsv(c["text"]),
                        model_version="bge-m3-v1",
                        sensitivity=lab["sensitivity"],
                        min_level=lab["min_level"],
                        classify_reason=lab["reason"],
                    )
                )
            _audit(
                db2,
                user_id,
                "upload_document",
                str(doc.id),
                {"title": filename, "source": "pdf", "chunks": len(chunk_data)},
            )
            db2.commit()
            db2.execute(
                _text("UPDATE pdf_tasks SET status='done', chunks=:n WHERE id=:id"),
                {"n": len(chunk_data), "id": task_id},
            )
            db2.commit()
        except Exception as e:
            db2.rollback()
            db2.execute(
                _text("UPDATE pdf_tasks SET status='failed', error=:e WHERE id=:id"),
                {"e": str(e), "id": task_id},
            )
            db2.commit()
        finally:
            db2.close()

    from app.core.pool import bg_pool

    bg_pool().submit(bg_task)  # 应用级池执行 PDF 解析（解析+embedding 长任务，低频）
    return {"task_id": task_id, "status": "pending"}


@router.get("/task/{task_id}")
def task_status(task_id: str, db: Session = Depends(get_db)):
    """查询任务状态"""
    row = db.execute(
        text("SELECT status, chunks, error FROM pdf_tasks WHERE id=:id"),
        {"id": task_id},
    ).fetchone()
    if not row:
        return {"status": "not_found"}
    return {"status": row[0], "chunks": row[1], "error": row[2]}


@router.post("/upload-images")
async def upload_images(
    files: list[UploadFile] = File(...),
    admin: User = Depends(require_admin),
):
    """md/txt 附带图片上传：存 MinIO + 返回 presigned URL 映射（按 basename 匹配 md 引用）"""
    result = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        md5_img = hashlib.md5(data).hexdigest()
        base = f.filename.replace("\\", "/").split("/")[-1]
        img_obj = f"images/{md5_img}/{base}"
        minio_client.put_object(
            BUCKET,
            img_obj,
            io.BytesIO(data),
            len(data),
            content_type=f.content_type or "image/png",
        )
        url = minio_client.presigned_get_object(BUCKET, img_obj, expires=timedelta(days=7))
        result.append({"ref_name": base, "url": url})
        logger.info(f"图片附件入库: {base} -> {url[:80]}...")
    return {"images": result}


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    category: str = None,
    image_urls: str = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """上传并入库（仅 admin 内容生产），自动敏感片段打标"""
    user_id = admin.id
    content = await file.read()
    filename = file.filename

    # MD5 去重：active 命中则已存在；软删命中则复活（释放唯一键占用，重传即恢复）
    md5 = hashlib.md5(content).hexdigest()
    exists = db.query(Document).filter(Document.md5_hash == md5, Document.is_deleted == False).first()
    if exists:
        return {"status": "exists", "doc_id": str(exists.id)}
    soft = db.query(Document).filter(Document.md5_hash == md5, Document.is_deleted == True).first()
    if soft:
        soft.is_deleted = False
        soft.deleted_at = None
        db.query(Chunk).filter(Chunk.doc_id == soft.id).update({"is_deleted": False})
        _audit(db, user_id, "restore_document", str(soft.id), {"title": filename, "source": "restore"})
        db.commit()
        logger.info(f"复活软删文档: {soft.id} title={soft.title}")
        chunk_count = db.query(Chunk).filter(Chunk.doc_id == soft.id, Chunk.is_deleted == False).count()
        return {"status": "ok", "doc_id": str(soft.id), "chunks": chunk_count, "restored": True}

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
                chunk_data.append(
                    {
                        "text": piece,
                        "chunk_type": c["type"],
                        "source_type": c["source_type"],
                    }
                )
        source = "jupyter"
    else:
        text = content.decode("utf-8", errors="ignore")
        # md/txt 携带图片附件：按 basename 匹配引用并替换为 presigned URL（预览可见）
        if image_urls:
            try:
                for img_map in json.loads(image_urls):
                    base = (img_map.get("ref_name") or "").replace("\\", "/").split("/")[-1]
                    url = img_map.get("url") or ""
                    if base and url:
                        text = re.sub(
                            rf"!\[[^\]]*\]\([^)]*{re.escape(base)}\)",
                            f"![图片]({url})",
                            text,
                        )
            except Exception as e:
                logger.warning(f"图片引用替换失败: {e}")
        # 分块前剥离 base64 data URL（无语义数据不进知识库/embedding，防超长序列 OOM；MinIO 仍存完整文本供预览）
        text_for_chunk = re.sub(
            r"data:image/[a-zA-Z0-9.+/]+;base64,[A-Za-z0-9+/=\s]+",
            "![图片](data-url)",
            text,
        )
        pieces = split_text(text_for_chunk)
        chunk_data = [{"text": p, "chunk_type": "text", "source_type": "markdown"} for p in pieces]
        source = "markdown" if filename.endswith(".md") else "text"

    if not chunk_data:
        return {"status": "empty"}

    # 事务外算 embedding
    texts = [c["text"] for c in chunk_data]
    embeddings = embed_texts(texts)

    # 上传到 MinIO（md/txt 存替换图片引用后的文本，保证预览与入库一致）
    object_name = f"{md5}/{filename}"
    store_bytes = text.encode("utf-8") if source in ("markdown", "text") else content
    minio_client.put_object(
        BUCKET,
        object_name,
        io.BytesIO(store_bytes),
        len(store_bytes),
        content_type=file.content_type or "application/octet-stream",
    )

    try:
        doc = Document(
            title=filename,
            category=category,
            source=source,
            md5_hash=md5,
            file_path=object_name,
            model_version="bge-m3-v1",
            user_id=user_id,
            visibility="internal",
            min_level=10,
        )
        db.add(doc)
        db.flush()

        # 敏感片段自动打标（标记语法 → 关键词 → LLM 兜底）
        labels = label_chunks_batch([c["text"] for c in chunk_data])
        chunk_objects = []
        for i, (c, emb) in enumerate(zip(chunk_data, embeddings)):
            lab = labels[i]
            chunk_objects.append(
                Chunk(
                    doc_id=doc.id,
                    chunk_index=i,
                    chunk_type=c["chunk_type"],
                    source_type=c["source_type"],
                    content=c["text"],
                    embedding=emb,
                    tsv=build_tsv(c["text"]),
                    model_version="bge-m3-v1",
                    sensitivity=lab["sensitivity"],
                    min_level=lab["min_level"],
                    classify_reason=lab["reason"],
                )
            )

        db.add_all(chunk_objects)
        _audit(
            db,
            user_id,
            "upload_document",
            str(doc.id),
            {"title": filename, "source": source, "chunks": len(chunk_data)},
        )
        db.commit()
        return {"status": "ok", "doc_id": str(doc.id), "chunks": len(chunk_data)}
    except Exception as e:
        db.rollback()
        raise e
