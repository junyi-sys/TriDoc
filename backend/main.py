"""
TriDoc — FastAPI 主应用。
日↔英↔中 文档翻译与润色平台。
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="TriDoc",
    description="日↔英↔中 文档翻译与润色平台 — DeepSeek 驱动",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
from routers import files, documents

app.include_router(files.router, prefix="/api", tags=["Files"])
app.include_router(documents.router, prefix="/api", tags=["Documents"])


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    from services.ai_service import DEEPSEEK_API_KEY
    return {
        "status": "ok",
        "version": "0.1.0",
        "engine": "DeepSeek",
        "api_configured": bool(DEEPSEEK_API_KEY),
    }
