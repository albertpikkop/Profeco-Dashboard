"""FastAPI router for RAG document query endpoints."""

import os
import shutil
import uuid
import logging
import sqlite3
import mimetypes

from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse

from rag.config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, DOCUMENTS_DIR, DB_PATH
from rag.engine import RAGEngine
from rag.models import QueryRequest, FeedbackRequest

logger = logging.getLogger(__name__)

rag_router = APIRouter(prefix="/api/rag", tags=["rag"])

# Singleton engine
_engine = None


def get_engine() -> RAGEngine:
    global _engine
    if _engine is None:
        _engine = RAGEngine()
    return _engine


@rag_router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    category: str = Query(default=None),
    source_org: str = Query(default=None),
    source_url: str = Query(default=None),
):
    """Upload and ingest a document into the knowledge base."""
    engine = get_engine()
    if not engine.is_configured():
        raise HTTPException(500, "OpenAI API key not configured. Set OPENAI_API_KEY in .env")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    doc_id = str(uuid.uuid4())
    safe_filename = f"{doc_id}_{file.filename}"
    save_path = os.path.join(DOCUMENTS_DIR, safe_filename)

    try:
        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        file_size = os.path.getsize(save_path)
        if file_size > MAX_FILE_SIZE:
            os.remove(save_path)
            raise HTTPException(400, f"File too large: {file_size} bytes (max {MAX_FILE_SIZE})")

        result = engine.ingest_document(
            save_path, file.filename, doc_id,
            category=category, source_org=source_org,
            source_url=source_url
        )
        return {"success": True, "data": result.model_dump()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(500, f"Failed to process document: {str(e)}")


@rag_router.post("/query")
async def query_documents(request: QueryRequest):
    """Ask a question against the knowledge base."""
    engine = get_engine()
    if not engine.is_configured():
        raise HTTPException(500, "OpenAI API key not configured. Set OPENAI_API_KEY in .env")

    if not request.question.strip():
        raise HTTPException(400, "Question cannot be empty")

    try:
        result = engine.query_hybrid(
            question=request.question,
            conversation_id=request.conversation_id,
            top_k=request.top_k,
            exclude_doc_ids=request.exclude_doc_ids
        )
        data = result.model_dump()
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Query failed: {e}")
        raise HTTPException(500, f"Query failed: {str(e)}")


@rag_router.get("/documents")
async def list_documents():
    """List all ingested documents."""
    engine = get_engine()
    docs = engine.list_documents()
    return {"success": True, "data": [d.model_dump() for d in docs]}


@rag_router.get("/documents/{doc_id}/file")
async def serve_document_file(doc_id: str):
    """Serve the original document file for viewing in a new tab."""
    engine = get_engine()
    filepath = engine.get_document_path(doc_id)
    if not filepath:
        raise HTTPException(404, "Document not found")

    media_type, _ = mimetypes.guess_type(filepath)
    if media_type is None:
        media_type = "application/octet-stream"

    return FileResponse(
        filepath,
        media_type=media_type,
        filename=os.path.basename(filepath),
        headers={"Content-Disposition": "inline"}  # display in browser, not download
    )


@rag_router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Remove a document from the knowledge base."""
    engine = get_engine()
    success = engine.delete_document(doc_id)
    if not success:
        raise HTTPException(404, "Document not found")
    return {"success": True, "message": "Document deleted"}


@rag_router.get("/history")
async def get_query_history(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get audit trail of all past queries."""
    engine = get_engine()
    history = engine.get_query_history(limit=limit, offset=offset)
    return {"success": True, "data": [h.model_dump() for h in history]}


@rag_router.get("/history/{query_id}")
async def get_query_detail(query_id: str):
    """Get detailed view of a specific query with all sources."""
    engine = get_engine()
    detail = engine.get_query_detail(query_id)
    if not detail:
        raise HTTPException(404, "Query not found")
    return {"success": True, "data": detail.model_dump()}


@rag_router.get("/briefing")
async def get_data_briefing():
    """Return a dynamic briefing of what data is available for the user to explore."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # egg_prices stats
        c.execute("SELECT COUNT(*) as n FROM egg_prices")
        total_rows = c.fetchone()["n"]

        c.execute("SELECT MIN(fecha_observacion) as min_date, MAX(fecha_observacion) as max_date FROM egg_prices")
        dates = c.fetchone()
        date_range = f"{dates['min_date']} al {dates['max_date']}" if dates["min_date"] else "Sin datos"

        c.execute("SELECT COUNT(DISTINCT estado) as n FROM egg_prices WHERE estado IS NOT NULL")
        state_count = c.fetchone()["n"]

        c.execute("SELECT DISTINCT region FROM egg_prices ORDER BY region")
        regions = [r["region"] for r in c.fetchall()]

        c.execute("SELECT marca, COUNT(*) as n FROM egg_prices GROUP BY marca ORDER BY n DESC LIMIT 5")
        top_brands = [{"name": r["marca"], "count": r["n"]} for r in c.fetchall()]

        c.execute("SELECT COUNT(DISTINCT cadena_comercial) as n FROM egg_prices")
        chain_count = c.fetchone()["n"]

        c.execute("SELECT ROUND(AVG(precio), 2) as avg, ROUND(MIN(precio), 2) as min, ROUND(MAX(precio), 2) as max FROM egg_prices")
        price_stats = dict(c.fetchone())

        # Wholesale stats
        wholesale_count = 0
        wholesale_markets = 0
        try:
            c.execute("SELECT COUNT(*) as n FROM wholesale_prices")
            wholesale_count = c.fetchone()["n"]
            c.execute("SELECT COUNT(DISTINCT mercado) as n FROM wholesale_prices")
            wholesale_markets = c.fetchone()["n"]
        except Exception:
            pass

        # RAG docs
        c.execute("SELECT filename, page_count FROM rag_documents WHERE status = 'ready' ORDER BY filename")
        reports = [{"name": r["filename"], "pages": r["page_count"]} for r in c.fetchall()]

        conn.close()

        # Build category cards
        categories = [
            {
                "id": "prices",
                "icon": "chart",
                "title": "Precios por Estado",
                "description": f"Compara precios promedio entre {state_count} estados. Ve donde es mas barato y mas caro en todo Mexico.",
                "sample": "Cual es el precio promedio del huevo por estado y donde es mas barato?",
            },
            {
                "id": "brands",
                "icon": "tag",
                "title": "Marcas y Competencia",
                "description": f"Analiza {len(top_brands)}+ marcas: Bachoco, San Juan, El Calvario y mas. Precios, presencia, posicionamiento.",
                "sample": "Comparame las 5 marcas principales de huevo por precio promedio y presencia en tiendas",
            },
            {
                "id": "wholesale",
                "icon": "sparkle",
                "title": "Mayoreo vs Menudeo",
                "description": f"Precios mayoreo de {wholesale_markets} centrales de abasto (SNIIM) vs {chain_count} cadenas retail (PROFECO).",
                "sample": "Como se comparan los precios mayoreo vs menudeo del huevo por estado?",
            },
            {
                "id": "regions",
                "icon": "map",
                "title": "Analisis Regional",
                "description": f"Explora {len(regions)} regiones: {', '.join(regions)}. Diferencias de precio y patrones.",
                "sample": "Cual es el precio promedio por region y cual region es la mas cara?",
            },
            {
                "id": "stores",
                "icon": "store",
                "title": "Cadenas Comerciales",
                "description": f"Ranking de {chain_count} cadenas: Walmart, Soriana, Bodega Aurrera... Donde comprar mas barato.",
                "sample": "Que cadenas comerciales tienen los precios mas bajos de huevo?",
            },
            {
                "id": "trends",
                "icon": "chart",
                "title": "Tendencias de Precios",
                "description": "Analiza como han cambiado los precios a lo largo del tiempo. Patrones semanales y mensuales.",
                "sample": "Como han cambiado los precios del huevo mes a mes durante 2025?",
            },
            {
                "id": "docs",
                "icon": "doc",
                "title": "Tus Reportes",
                "description": f"{len(reports)} documentos cargados. Sube PDFs, Word o PowerPoint para consultar con IA.",
                "sample": "Que informacion contienen los documentos que he subido?",
            },
        ]

        return {
            "success": True,
            "data": {
                "summary": {
                    "total_observations": total_rows,
                    "date_range": date_range,
                    "state_count": state_count,
                    "regions": regions,
                    "top_brands": top_brands,
                    "chain_count": chain_count,
                    "price_stats": price_stats,
                    "report_count": len(reports),
                    "wholesale_count": wholesale_count,
                    "wholesale_markets": wholesale_markets,
                },
                "categories": categories,
            }
        }
    except Exception as e:
        logger.error(f"Briefing failed: {e}")
        return {"success": True, "data": {"summary": {}, "categories": []}}


@rag_router.post("/feedback")
async def submit_feedback(request: FeedbackRequest):
    """Record user feedback (thumbs up/down) for a query answer."""
    if request.rating not in ("up", "down"):
        raise HTTPException(400, "Rating must be 'up' or 'down'")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM rag_queries WHERE id = ?", (request.query_id,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(404, "Query not found")
        cursor.execute("DELETE FROM rag_feedback WHERE query_id = ?", (request.query_id,))
        cursor.execute(
            "INSERT INTO rag_feedback (query_id, rating) VALUES (?, ?)",
            (request.query_id, request.rating)
        )
        conn.commit()
        conn.close()
        return {"success": True, "data": {"query_id": request.query_id, "rating": request.rating}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Feedback failed: {str(e)}")


@rag_router.get("/status")
async def get_rag_status():
    """Health check: document count, chunk count, API status."""
    engine = get_engine()
    status = engine.get_status()
    return {"success": True, "data": status.model_dump()}
