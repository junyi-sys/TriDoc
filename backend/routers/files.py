"""
TriDoc 文件上传路由。
"""

import os
import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException

logger = logging.getLogger("tri_doc.files")

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".pptx", ".docx"}
MAX_SIZE = 100 * 1024 * 1024  # 100 MB


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文档（PDF / PPTX / DOCX）。"""
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(400, f"File exceeds {MAX_SIZE // 1024 // 1024}MB limit")

    file_id = f"{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / file_id
    with open(file_path, "wb") as f:
        f.write(contents)

    logger.info("上传完成: %s → %s (%.1f KB)", file.filename, file_id, len(contents) / 1024)

    return {
        "file_id": file_id,
        "file_name": file.filename,
        "file_type": ext[1:],
        "size_bytes": len(contents),
    }


@router.get("/files")
def list_files():
    """列出已上传的文件。"""
    files_list: list[dict] = []
    for f in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix.lower() in ALLOWED_EXTENSIONS:
            files_list.append({
                "file_id": f.name,
                "file_name": f.name,
                "file_type": f.suffix[1:],
                "size_bytes": f.stat().st_size,
            })
    return {"files": files_list[:50]}


@router.delete("/files/{file_id}")
def delete_file(file_id: str):
    """删除上传的文件及关联数据。"""
    file_path = UPLOAD_DIR / file_id
    sidecar_path = UPLOAD_DIR / f"{file_id}.json"

    deleted = []
    if file_path.exists():
        file_path.unlink()
        deleted.append("file")
    if sidecar_path.exists():
        sidecar_path.unlink()
        deleted.append("sidecar")

    return {"deleted": deleted}
