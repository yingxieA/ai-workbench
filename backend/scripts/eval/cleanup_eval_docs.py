# -*- coding: utf-8 -*-
"""
清理测评数据：删除 [EVAL] 测试文档（数据库记录 + 可选 chunks）
=========================================================
默认只逻辑删除 Document（is_deleted=True），不动 chunks 物理数据；
--hard 时同时物理删除 chunks。

用法：
    python cleanup_eval_docs.py           # 逻辑删除
    python cleanup_eval_docs.py --hard    # 物理删除 doc + chunks
    python cleanup_eval_docs.py --dry-run # 只看有哪些
"""

import argparse
import sys

sys.path.insert(0, r"D:\study\ai_study\proj\ai-workbench\backend")

from app.database import SessionLocal  # noqa: E402
from app.models.document import Document, Chunk  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hard", action="store_true", help="物理删除 doc + chunks")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        docs = db.query(Document).filter(Document.filename.like("%[EVAL]%")).all()
        print(f"找到 [EVAL] 文档: {len(docs)} 份")
        for d in docs:
            print(f"  doc_id={d.id}  filename={d.filename}  user_id={d.user_id}")
        if args.dry_run:
            return
        if not docs:
            return
        if args.hard:
            for d in docs:
                n = db.query(Chunk).filter(Chunk.doc_id == d.id).delete(synchronize_session=False)
                db.delete(d)
                print(f"  物理删除 {d.filename} (chunks={n})")
            db.commit()
            print("完成：doc + chunks 已物理删除")
        else:
            for d in docs:
                d.is_deleted = True
            db.commit()
            print("完成：已逻辑删除（is_deleted=True）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
