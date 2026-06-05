"""MD5-based file conversion cache with 7-day TTL and LRU eviction."""

from __future__ import annotations

import hashlib
import os
import time
from io import BytesIO
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / "conversions"
CACHE_TTL_SECONDS = 7 * 24 * 3600
MAX_CACHE_SIZE_MB = 500


class FileCache:
    @staticmethod
    def _key(file_id: str, target_format: str) -> str:
        raw = f"{file_id}:{target_format}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, file_id: str, target_format: str) -> BytesIO | None:
        key = self._key(file_id, target_format)
        for candidate in CACHE_DIR.glob(f"{key}.*"):
            age = time.time() - candidate.stat().st_mtime
            if age > CACHE_TTL_SECONDS:
                candidate.unlink()
                return None
            return BytesIO(candidate.read_bytes())
        return None

    def put(self, file_id: str, target_format: str, data: bytes, ext: str):
        self._evict_if_needed(len(data))
        key = self._key(file_id, target_format)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}{ext}").write_bytes(data)

    def _evict_if_needed(self, incoming_size: int):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(CACHE_DIR.iterdir(), key=lambda f: f.stat().st_atime)
        total = sum(f.stat().st_size for f in files)
        limit = MAX_CACHE_SIZE_MB * 1024 * 1024
        while total + incoming_size > limit and files:
            victim = files.pop(0)
            total -= victim.stat().st_size
            victim.unlink()
