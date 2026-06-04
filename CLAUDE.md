# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TriDoc is a document translation and polish platform supporting Japanese ↔ English ↔ Chinese, powered by the DeepSeek API. Users upload PDF, PPTX, or DOCX files, optionally edit extracted text, run an AI pipeline that translates and normalizes terminology, then export translated or bilingual versions.

## Commands

```bash
# Start both servers (Windows)
start.bat

# Backend (manual)
cd backend && python -m uvicorn main:app --reload --port 8086

# Frontend (manual)
cd frontend && npx vite --port 5173

# Run tests
cd backend && python -m pytest tests/ -v -s

# Run tests with DeepSeek API (optional)
cd backend && DEEPSEEK_API_KEY=sk-xxx python -m pytest tests/test_pipeline.py -v -s

# Run edge case self-test (no API needed)
cd backend && python tests/test_edge_cases.py
```

Backend runs on `http://localhost:8086` (API docs at `/docs`), frontend on `http://localhost:5173`. Vite proxies `/api` requests to the backend.

## Architecture

### Backend (FastAPI)

**Request flow**: `routers/` (HTTP layer) → `services/` (business logic) → sidecar JSON files in `backend/uploads/` (persistence).

**Key modules in `backend/services/`**:

- `ai_service.py` — DeepSeek API client via OpenAI-compatible protocol. Single model strategy (`deepseek-chat`). Built-in 3-retry loop (skips 4xx). Three temperature presets: `TEMP_PRECISE` (0.1), `TEMP_BALANCED` (0.3), `TEMP_CREATIVE` (0.5).

- `ai_pipeline.py` — 6-stage translation pipeline orchestrator. `run_pipeline()` runs stages sequentially with graceful degradation (errors in one stage don't stop the rest):
  - **Stage 0**: Glossary match (forced string replace from user glossary)
  - **Stage 1**: Source polish (AI-native editing per language)
  - **Stage 2**: Page-by-page translation to each target language
  - **Stage 2.5**: Context alignment — the key quality step. TF-IDF extracts candidate terms → GPT groups variants by concept → normalizer picks standard forms → global regex replace across all pages
  - **Stage 3**: Post-translation polish (naturalize output)
  - **Stage 4**: Glossary verify (force glossary terms back if AI rewrote them)

- `document_parser.py` — Unified parser dispatching to PyMuPDF (PDF), python-pptx (PPTX), python-docx (Word). Returns a common `{pages: [{page, text, blocks}]}` format where `blocks` preserve positional and style info.

- `export_service.py` — Writes translated text back into the original file format. Supports `translated` (single-language) and `bilingual` (side-by-side) modes.

- `term_extractor.py` — TF-IDF-based n-gram extraction with GPT-enhanced semantic grouping. Has a `_fallback_grouping()` path when GPT is unavailable (substring/prefix heuristics).

- `term_normalizer.py` — Standardizes term variants with priority: user glossary > frequency > first-occurrence > GPT arbitration.

- `term_replacer.py` — Regex-based global find-and-replace across pages, sorted by term length descending to avoid substring collisions. Uses `\b` word boundaries for single tokens.

**State management**: All per-document state (extracted pages, user edits, translations, final output) is stored in sidecar JSON files (`backend/uploads/<file_id>.json`). The routers layer loads/saves these; services operate on in-memory data structures.

### Frontend (React + Vite)

`App.jsx` manages all state: current file, extracted pages, selected page index, source language, translations. Components are straightforward:
- `FileUpload` → triggers upload then extraction
- `PageList` → page thumbnails/sidebar
- `TextEditor` → edit text for the selected page
- `AIPanel` → configure language targets and glossary, trigger translation
- `ExportPanel` → download translated/bilingual file

API calls are centralized in `src/api/index.js` using axios.

### Environment

Configuration is loaded from `backend/.env` (via `python-dotenv`). Key variables:

| Variable | Default | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | (required) | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API endpoint |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name |
| `PORT` | `8086` | Backend server port |
| `HOST` | `0.0.0.0` | Backend bind address |

Without `DEEPSEEK_API_KEY`, the TF-IDF fallback in `term_extractor.py` still works, but translation and GPT-powered term grouping will fail.
