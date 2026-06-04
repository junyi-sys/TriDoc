"""
TriDoc 文档路由 — 提取 / AI 管线 / 导出。

端点:
  GET  /api/extract/{file_id}            → 解析文档，返回页面结构
  PUT  /api/extract/{file_id}            → 保存用户编辑
  POST /api/ai/polish/{file_id}          → 润色原文
  POST /api/ai/translate/{file_id}       → 翻译（触发完整管线）
  POST /api/ai/align/{file_id}           → 独立执行 Stage 2.5 上下文对齐
  GET  /api/export/{file_id}             → 导出翻译/对照版文件
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.document_parser import parse_document
from services.ai_pipeline import run_pipeline
from services.export_service import export_document

logger = logging.getLogger("tri_doc.api")

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"


# ============================================================================
# 数据模型
# ============================================================================
class PageEdit(BaseModel):
    page: int
    text: str


class ExtractSaveRequest(BaseModel):
    pages: list[PageEdit] = []
    source_language: str = "ja"


class TranslateRequest(BaseModel):
    source_language: str = "ja"
    target_languages: list[str] = ["en"]
    glossary: dict[str, str] = {}
    skip_polish: bool = False
    skip_post_polish: bool = False


class AlignRequest(BaseModel):
    target_language: str = "en"
    glossary: dict[str, str] = {}


# ============================================================================
# 辅助
# ============================================================================
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


# ============================================================================
# 提取
# ============================================================================
@router.get("/extract/{file_id}")
def extract_document(file_id: str):
    """解析文档，返回页面级结构化内容。"""
    try:
        result = parse_document(file_id)
        # 合并 sidecar 中的编辑/翻译数据
        sidecar = _load_sidecar(file_id)
        if sidecar:
            result["sidecar"] = {
                "source_language": sidecar.get("source_language", ""),
                "has_edits": "edited" in sidecar,
                "translations": list(sidecar.get("translations", {}).keys()),
                "final": list(sidecar.get("final", {}).keys()),
            }
        return result
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    except Exception as e:
        logger.exception("解析失败: %s", e)
        raise HTTPException(500, f"Parse error: {e}")


@router.put("/extract/{file_id}")
def save_edits(file_id: str, req: ExtractSaveRequest):
    """保存用户编辑到 sidecar。"""
    sidecar = _load_sidecar(file_id)
    sidecar["file_id"] = file_id
    sidecar["source_language"] = req.source_language

    # 保存原始内容（首次）
    if "original" not in sidecar:
        try:
            parsed = parse_document(file_id)
            sidecar["original"] = {"pages": parsed["pages"]}
        except Exception:
            pass

    sidecar["edited"] = {"pages": [p.model_dump() for p in req.pages]}
    _save_sidecar(file_id, sidecar)
    return {"status": "saved", "pages": len(req.pages)}


# ============================================================================
# AI 管线
# ============================================================================
@router.post("/ai/polish/{file_id}")
def polish_document(file_id: str, req: TranslateRequest):
    """仅润色原文（Stage 0 + Stage 1）。"""
    sidecar = _load_sidecar(file_id)
    pages = _get_source_pages(file_id, sidecar)

    result = run_pipeline(
        file_id=file_id,
        pages=pages,
        source_lang=req.source_language,
        target_langs=[req.source_language],  # 只润色原文
        glossary=req.glossary,
        skip_polish=False,
        skip_post_polish=True,
    )

    # 保存结果
    sidecar["polished"] = {"pages": result.final_pages.get(req.source_language, pages)}
    sidecar["consistency_report"] = result.consistency_report
    _save_sidecar(file_id, sidecar)

    return {
        "status": "done",
        "polished_pages": len(pages),
        "consistency": result.consistency_report,
    }


@router.post("/ai/translate/{file_id}")
def translate_document(file_id: str, req: TranslateRequest):
    """翻译文档 — 执行完整 AI 管线（Stage 0→1→2→2.5→3→4）。"""
    sidecar = _load_sidecar(file_id)
    pages = _get_source_pages(file_id, sidecar)

    result = run_pipeline(
        file_id=file_id,
        pages=pages,
        source_lang=req.source_language,
        target_langs=req.target_languages,
        glossary=req.glossary,
        skip_polish=req.skip_polish,
        skip_post_polish=req.skip_post_polish,
    )

    # 保存翻译结果
    sidecar["source_language"] = req.source_language
    if "translations" not in sidecar:
        sidecar["translations"] = {}
    for lang, pages in result.final_pages.items():
        sidecar["translations"][lang] = pages

    if "final" not in sidecar:
        sidecar["final"] = {}
    for lang, pages in result.final_pages.items():
        sidecar["final"][lang] = pages

    sidecar["consistency_report"] = result.consistency_report
    sidecar["stats"] = result.stats
    _save_sidecar(file_id, sidecar)

    return {
        "status": "done" if result.success else "partial",
        "target_languages": result.target_langs,
        "stats": result.stats,
        "consistency": result.consistency_report,
        "errors": result.errors,
    }


@router.post("/ai/align/{file_id}")
def align_terms(file_id: str, req: AlignRequest):
    """独立执行 Stage 2.5 上下文对齐（已有翻译时使用）。"""
    sidecar = _load_sidecar(file_id)
    translations = sidecar.get("translations", {})
    pages = translations.get(req.target_language)

    if not pages:
        raise HTTPException(404, f"No translation found for {req.target_language}")

    # 直接调用 Stage 2.5
    from services.term_extractor import extract_term_variants_gpt
    from services.term_normalizer import normalize_terms
    from services.term_replacer import global_replace

    term_groups = extract_term_variants_gpt(pages=pages, page_texts=pages, language=req.target_language)
    norm = normalize_terms(term_groups, glossary=req.glossary, language=req.target_language)
    repl = global_replace(pages, norm.replacement_map)

    # 更新 sidecar
    if "aligned" not in sidecar:
        sidecar["aligned"] = {}
    sidecar["aligned"][req.target_language] = repl.pages
    _save_sidecar(file_id, sidecar)

    return {
        "status": "done",
        "terms_total": norm.total_terms,
        "terms_normalized": norm.normalized,
        "replacements_made": repl.total_replacements,
        "pages_modified": repl.pages_modified,
        "replacement_map": norm.replacement_map,
        "details": norm.details,
    }


# ============================================================================
# 导出
# ============================================================================
@router.get("/export/{file_id}")
def export_translated(
    file_id: str,
    target_language: str = Query(..., description="ja / en / zh"),
    mode: str = Query("translated", description="translated | bilingual"),
):
    """导出翻译版或对照版文件。"""
    try:
        buf = export_document(file_id, target_language, mode)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    ext = Path(file_id).suffix.lower()
    media_types = {
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    filename = f"exported_{target_language}_{mode}{ext}"

    return StreamingResponse(
        buf,
        media_type=media_types.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================================
# 辅助
# ============================================================================
def _get_source_pages(file_id: str, sidecar: dict) -> list[dict]:
    """获取源页面（编辑版 > 原文）。"""
    edited = sidecar.get("edited", {}).get("pages")
    if edited:
        return edited
    original = sidecar.get("original", {}).get("pages")
    if original:
        return original
    parsed = parse_document(file_id)
    return parsed["pages"]
