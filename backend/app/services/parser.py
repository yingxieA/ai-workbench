"""文档解析服务"""
import json
import io
import uuid
import asyncio
import zipfile
import httpx
from app.config import settings


async def parse_pdf(file_bytes: bytes, filename: str = "doc.pdf") -> str:
    """调 MinerU API 解析 PDF，返回 Markdown 文本"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.MINERU_API_KEY}"
    }
    data_id = uuid.uuid4().hex[:16]

    async with httpx.AsyncClient(timeout=120) as client:
        # 1. 申请上传 URL
        resp = await client.post(
            f"{settings.MINERU_API_URL}/file-urls/batch",
            headers=headers,
            json={
                "files": [{"name": filename, "data_id": data_id}],
                "model_version": "vlm",
                "language": "ch"
            }
        )
        result = resp.json()
        if result.get("code") != 0:
            return f"申请上传失败: {result.get('msg')}"

        batch_id = result["data"]["batch_id"]
        upload_url = result["data"]["file_urls"][0]

        # 2. PUT 上传文件
        put_resp = await client.put(upload_url, content=file_bytes)
        if put_resp.status_code not in (200, 201):
            return "文件上传失败"

        # 3. 轮询结果
        for _ in range(60):
            await asyncio.sleep(3)
            result_resp = await client.get(
                f"{settings.MINERU_API_URL}/extract-results/batch/{batch_id}",
                headers=headers
            )
            batch_data = result_resp.json().get("data", {})
            results = batch_data.get("extract_result", [])
            if results:
                state = results[0].get("state")
                if state == "done":
                    zip_url = results[0].get("full_zip_url")
                    if zip_url:
                        zip_resp = await client.get(zip_url)
                        z = zipfile.ZipFile(io.BytesIO(zip_resp.content))
                        md = ""
                        for name in z.namelist():
                            if name.endswith(".md"):
                                md += z.read(name).decode("utf-8", errors="ignore") + "\n\n"
                        return md
                elif state == "failed":
                    return "解析失败"
        return "解析超时"


def parse_ipynb(file_bytes: bytes) -> list[dict]:
    """解析 Jupyter Notebook，分离 Cell"""
    nb = json.loads(file_bytes.decode("utf-8"))
    cells = []
    for cell in nb.get("cells", []):
        cell_type = cell.get("cell_type")
        source = "".join(cell.get("source", []))
        if cell_type == "markdown":
            cells.append({"type": "markdown", "source_type": "jupyter_markdown", "content": source})
        elif cell_type == "code":
            cells.append({"type": "code", "source_type": "jupyter_code", "content": source})
            for out in cell.get("outputs", []):
                text = ""
                if "text" in out:
                    text = "".join(out["text"])
                elif "data" in out and "text/plain" in out["data"]:
                    text = "".join(out["data"]["text/plain"])
                if text:
                    cells.append({"type": "output", "source_type": "jupyter_output", "content": text[:500]})
    return cells
