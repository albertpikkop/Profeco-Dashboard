# CLAUDE.md - Egg Price BI Dashboard + RAG Document Query Portal

## Project Overview

Two-part application:
1. **Egg Price Dashboard** — BI dashboard for visualizing egg prices across Mexico using PROFECO data
2. **RAG Document Query Portal** — Chat-based interface for querying market research documents (PDFs, Word, PowerPoint) with AI-powered answers and source citations

## Tech Stack

- **Backend:** Python 3.11+ / FastAPI / Uvicorn / SQLite3
- **Frontend:** Vanilla HTML/JS, Tailwind CSS (CDN), Plotly.js 2.27.0 (CDN)
- **AI/ML:** OpenAI API (GPT-5.2-codex for generation, text-embedding-3-small for embeddings)
- **Vector Store:** SQLite + numpy (cosine similarity search over stored embeddings)
- **Document Processing:** pdfplumber, python-docx, python-pptx, pytesseract (OCR fallback)
- **Report Generation:** reportlab (PDF creation from database queries)
- **External APIs:** PROFECO prices API, OpenAI API
- **Deployment:** Replit → Google Cloud Run (port 8000 local, 80 external)

## Project Structure

```
main.py              # FastAPI app — dashboard + RAG routes (770 lines)
fetcher.py           # PROFECO data fetching, parsing, brand/qty extraction (599 lines)
analytics.py         # Statistical utilities (368 lines, not yet wired into API)
requirements.txt     # 13 deps
static/
  index.html         # Price dashboard SPA (1060 lines)
  chat.html          # RAG document query portal (420 lines)
rag/                 # RAG module
  __init__.py
  config.py          # Environment vars, constants (OPENAI_API_KEY, chunk size, etc.)
  models.py          # Pydantic request/response schemas
  ingest.py          # Document text extraction (PDF/DOCX/PPTX) + chunking
  vectorstore.py     # SQLite-based vector store with numpy cosine similarity
  engine.py          # RAG orchestration: embed → retrieve → prompt → LLM → audit log
  router.py          # FastAPI APIRouter with /api/rag/* endpoints
scripts/
  load_poc_data.py           # Download & ingest public PROFECO/SciELO reports for POC
  generate_market_reports.py # Generate PDF reports from PROFECO API data & ingest into RAG
data/
  eggs.db            # SQLite database (auto-created on startup)
  documents/         # Uploaded/downloaded document files
.env                 # OPENAI_API_KEY (gitignored)
.replit              # Replit config with Cloud Run deployment target
replit.nix           # Nix environment definition
```

## Quick Start

```bash
pip install -r requirements.txt
# Create .env with: OPENAI_API_KEY=sk-...
python main.py
# Server starts at http://0.0.0.0:8000
# Dashboard: http://localhost:8000/
# Chat Portal: http://localhost:8000/chat
```

To load POC demo data (PROFECO quality studies, SciELO market analysis):
```bash
python scripts/load_poc_data.py
```

To generate market intelligence reports from PROFECO price data & ingest into RAG:
```bash
python scripts/generate_market_reports.py
```
Generates 5 professional PDF reports from the `egg_prices` database:
- National Market Overview (cities, brands, pack sizes)
- Competitive Intelligence (Bachoco vs San Juan, market share)
- Distribution Channels (store chain rankings, best/worst deals)
- Regional Analysis (4 regions, city vs national benchmarks)
- Bachoco Deep Dive (positioning, store distribution, product catalog)

## Key API Endpoints

### Dashboard Endpoints
| Endpoint | Description |
|----------|-------------|
| `GET /` | Serve dashboard HTML |
| `GET /chat` | Serve RAG chat portal HTML |
| `GET /api/summary` | KPIs and executive summary |
| `GET /api/prices` | Filtered price data |
| `GET /api/cities` | City rankings with statistics |
| `GET /api/chains` | Store chain rankings by city |
| `GET /api/brands` | Brand analysis and market share |
| `GET /api/packs` | Pack size analysis with price per egg |
| `GET /api/regions` | Regional price aggregates |
| `GET /api/best-deals` | Lowest price-per-egg products |
| `GET /api/data-quality` | Data completeness metrics |
| `GET /api/map-data` | Geographic visualization data |
| `GET /api/data-status` | Database status (record count, date range) |
| `POST /api/fetch-fresh` | Trigger background data refresh |

### RAG Endpoints
| Endpoint | Description |
|----------|-------------|
| `POST /api/rag/upload` | Upload and ingest a document (PDF/DOCX/PPTX) |
| `POST /api/rag/query` | Ask a question — returns answer + source citations |
| `GET /api/rag/documents` | List all ingested documents with metadata |
| `GET /api/rag/documents/{id}/file` | Serve original file (opens in new tab) |
| `DELETE /api/rag/documents/{id}` | Remove document from knowledge base |
| `GET /api/rag/history` | Audit trail — all past queries with answers |
| `GET /api/rag/history/{id}` | Detail view of a specific query with sources |
| `GET /api/rag/status` | Health check (doc count, chunk count, API status) |

## Database

### Dashboard Tables
- `egg_prices` — PROFECO price data with 8 indexes, UNIQUE constraint
- `cities` — 8 city codes mapped to names and regions

### RAG Tables
- `rag_documents` — document registry (filename, format, category, source_org, source_url, status)
- `rag_chunks` — text chunks with numpy embedding blobs
- `rag_queries` — audit log of every query and answer
- `rag_query_sources` — sources cited per query (doc, page, snippet, relevance)
- `rag_conversations` — conversation sessions
- `rag_messages` — message history per conversation (for follow-up context)

## RAG Pipeline

1. **Upload** → Save file to `data/documents/`, store `source_url` for original link
2. **Extract** → pdfplumber (PDF), python-docx (DOCX), python-pptx (PPTX)
3. **OCR fallback** → If page has < 50 chars, use pytesseract
4. **Chunk** → Split into ~500-token chunks with 50-token overlap, paragraph-aware
5. **Embed** → OpenAI `text-embedding-3-small` (1536 dimensions)
6. **Store** → Embeddings as numpy blobs in SQLite `rag_chunks` table
7. **Query** → Embed question → cosine similarity search → top-K chunks
8. **Generate** → GPT-4o-mini with context chunks + conversation history (last 10 messages)
9. **Cite** → `[Source: filename, page X]` format in answers, deduplicated citation cards
10. **Audit** → Log query, answer, and all sources to SQLite

## Chat Features

- **Conversation memory** — follow-up questions retain context (last 10 messages)
- **Bilingual** — responds in Spanish or English matching the user's language
- **Source citations** — each answer shows clickable citation cards (document, page, snippet, relevance %)
- **Citation deduplication** — same document+page shown only once
- **Document directory** — sidebar lists all documents with "Fuente original" link to source URL
- **PDF viewer** — clicking a citation opens the PDF in a new tab at the exact page
- **Audit trail** — full query history with timestamps, browsable via "Historial" tab
- **Drag-and-drop upload** — add new documents via the sidebar

## Code Patterns & Conventions

- **Database access:** Context manager `get_db()` with `sqlite3.Row` row factory
- **API endpoints:** Async FastAPI handlers returning `{"success": True, "data": ...}`
- **RAG module:** Separate `rag/` package with APIRouter, mounted via `app.include_router()`
- **Data extraction:** Regex pattern matching with fallback chains (see `fetcher.py`)
- **Naming:** Python snake_case, JS camelCase, Spanish names acceptable for domain terms
- **Error handling:** Try/except with logging, retry with exponential backoff in fetcher
- **Frontend rendering:** `.innerHTML` template literals, vanilla JS, Tailwind CDN

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes (for RAG) | OpenAI API key for embeddings + LLM |

Set in `.env` file at project root (gitignored).

## Knowledge Base — POC Data Sources

The POC knowledge base contains two types of documents:

### 1. Downloaded Public Reports (via `load_poc_data.py`)
| Report | Source | Pages | Category |
|--------|--------|-------|----------|
| Laboratorio Profeco Huevo | PROFECO | 10 | Quality |
| Estudio Huevo 2016 | PROFECO | 17 | Consumer |
| Analisis Mercado Huevo 1975-2020 | SciELO | 23 | Market Analysis |
| Estudios Calidad Huevo RC476 | PROFECO | 9 | Quality |
| Revista Consumidor 544 Jun 2022 | PROFECO | 70 | Consumer |

### 2. Generated Market Intelligence Reports (via `generate_market_reports.py`)
| Report | Content | Category |
|--------|---------|----------|
| Panorama Nacional Huevo Mexico | City rankings, brand analysis, pack sizes | Market Intelligence |
| Inteligencia Competitiva Marcas | Bachoco vs San Juan, market share, distribution | Market Intelligence |
| Canales Distribucion Huevo | Store chain rankings, best/worst deals | Market Intelligence |
| Analisis Regional Precios | 4 regions, city vs national benchmarks | Market Intelligence |
| Bachoco Analisis Detallado | Positioning, store distribution, product catalog | Market Intelligence |

All reports cite `https://qqp.profeco.gob.mx/` as the data source. Generated reports are created as professional PDFs using reportlab.

## Important Notes

- `analytics.py` contains statistical functions not yet integrated into API
- PROFECO API has no auth but is rate-limited — 1-second delays between cities
- RAG vector store uses SQLite + numpy (not ChromaDB) for Python 3.14 compatibility
- OCR requires `tesseract-ocr` and `poppler-utils` system packages
- Cloud Run disk is ephemeral — run `load_poc_data.py` and `generate_market_reports.py` after each deploy
- No automated tests exist yet
- CORS is enabled for all origins
- No user authentication (single-user POC)
- Duplicate document filenames are rejected at ingestion time

## Git & Deployment

- **Branch:** `main`
- **Remote:** GitHub (`albertpikkop/Profeco-Dashboard`)
- **CI/CD:** None configured — deployment via Replit Cloud Run integration
- **Commit style:** Conventional-ish (`fix:`, `feat:`)
