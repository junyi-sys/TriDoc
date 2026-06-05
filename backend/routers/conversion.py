"""TriDoc 文件格式转换路由 — PDF/DOCX/PPTX/TXT 互转。"""

from __future__ import annotations

import json
import logging
import threading
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.conversion_service import (
    convert_document,
    is_async,
    get_target_formats,
    _source_ext,
)

logger = logging.getLogger("tri_doc.convert")

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"


class ConvertRequest(BaseModel):
    target_format: str  # pdf / docx / pptx / txt


# ---------------------------------------------------------------------------
# Helpers (same pattern as documents.py)
# ---------------------------------------------------------------------------
def _load_sidecar(file_id: str) -> dict:
    path = UPLOAD_DIR / f"{file_id}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_sidecar(file_id: str, data: dict):
    path = UPLOAD_DIR / f"{file_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/convert/{file_id}")
def start_conversion(file_id: str, req: ConvertRequest):
    """Start a conversion. P0 returns immediately; P1/P2 run async."""
    target = req.target_format.lower().strip()

    src_ext = _source_ext(file_id)
    valid = get_target_formats(src_ext)
    if target not in valid:
        raise HTTPException(400, f"Unsupported: {src_ext} → {target}. Valid targets: {valid}")

    sidecar = _load_sidecar(file_id)

    if is_async(file_id, target):
        sidecar["conversion_status"] = {"running": True, "stage": "准备中", "progress": 0, "target_format": target}
        _save_sidecar(file_id, sidecar)

        def _run():
            try:
                def _progress(stage, cur, total):
                    sidecar["conversion_status"] = {
                        "running": True, "stage": stage, "progress": int(cur / total * 100),
                        "target_format": target,
                    }
                    _save_sidecar(file_id, sidecar)

                data, ext = convert_document(file_id, target, progress_callback=_progress)
                sidecar["conversion_status"] = {
                    "running": False, "stage": "完成", "progress": 100,
                    "target_format": target, "cached": True,
                }
                _save_sidecar(file_id, sidecar)
            except Exception as exc:
                logger.exception("Conversion failed: %s", exc)
                sidecar["conversion_status"] = {
                    "running": False, "stage": "失败", "progress": 0,
                    "target_format": target, "error": str(exc),
                }
                _save_sidecar(file_id, sidecar)

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "started", "async": True}
    else:
        try:
            data, ext = convert_document(file_id, target)
            sidecar["conversion_status"] = {
                "running": False, "stage": "完成", "progress": 100,
                "target_format": target, "cached": True,
            }
            _save_sidecar(file_id, sidecar)
            return {"status": "done", "async": False, "download_ready": True}
        except Exception as exc:
            logger.exception("Conversion failed: %s", exc)
            raise HTTPException(500, f"Conversion error: {exc}")


@router.get("/convert/status/{file_id}")
def conversion_status(file_id: str):
    """Poll async conversion progress."""
    sidecar = _load_sidecar(file_id)
    status = sidecar.get("conversion_status")
    if not status:
        return {"running": False, "stage": "未开始", "progress": 0}
    return dict(status)


@router.get("/convert/download/{file_id}")
def download_conversion(
    file_id: str,
    target_format: str = Query("", description="pdf / docx / pptx / txt — inferred from last conversion if omitted"),
):
    """Download the converted file from cache."""
    sidecar = _load_sidecar(file_id)
    target = target_format or sidecar.get("conversion_status", {}).get("target_format", "")

    if not target:
        raise HTTPException(404, "No conversion found")

    from cache.file_cache import FileCache
    cache = FileCache()
    data = cache.get(file_id, target)
    if data is None:
        raise HTTPException(404, "Cached result not found — re-run conversion")

    ext_map = {"pdf": ".pdf", "docx": ".docx", "pptx": ".pptx", "txt": ".txt"}
    ext = ext_map.get(target, f".{target}")
    media_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
    }

    return StreamingResponse(
        BytesIO(data.getvalue()),
        media_type=media_types.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="converted_{file_id}{ext}"'},
    )


@router.get("/convert/formats")
def list_formats():
    """List all supported conversions and valid target formats per source type."""
    from services.conversion_service import SUPPORTED_CONVERSIONS
    by_source: dict[str, list[str]] = {}
    for (src, tgt) in SUPPORTED_CONVERSIONS:
        by_source.setdefault(src, []).append(tgt)
    return {
        "conversions": [f"{s}→{t}" for (s, t) in SUPPORTED_CONVERSIONS],
        "by_source": by_source,
        "note": "P0 (<2s sync): any→TXT. P1 (sync ≤20MB): PDF↔DOCX, TXT→PDF/DOCX. P2 (async): PDF↔PPTX.",
    }
