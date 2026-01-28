"""RAG engine: orchestrates embedding, retrieval, prompt construction, and LLM generation."""

import os
import re
import uuid
import json
import sqlite3
import logging
from typing import List, Optional, Tuple

from openai import OpenAI

from rag.config import (
    OPENAI_API_KEY, EMBEDDING_MODEL, LLM_MODEL,
    TOP_K_RESULTS, DOCUMENTS_DIR, MAX_HISTORY_MESSAGES, DB_PATH
)
from rag.ingest import process_document, get_page_count, Chunk
from rag.vectorstore import (
    get_db, init_rag_tables, add_chunks, query_similar,
    delete_document_chunks, get_document_chunk_count, SearchResult
)
from rag.models import (
    QueryResponse, SourceReference, DocumentUploadResponse,
    DocumentInfo, QueryHistoryItem, QueryHistoryDetail, RAGStatus
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a market research analyst assistant for a Mexican food industry consulting firm.
You answer questions based ONLY on the provided document context.
If the context does not contain enough information to answer fully, say so clearly and mention what information is available.
Always cite your sources using numbered references like [1], [2], etc. The number corresponds to the context chunk number provided below. You may cite the same source multiple times. Never invent source numbers beyond those provided.
Answer in the same language the user asks in (Spanish or English).
Be precise with numbers, dates, and statistics — quote them exactly as they appear in the sources.

IMPORTANT: At the end of your answer, always add exactly 3 follow-up questions the user might want to ask next. Format them as:
---SUGGESTIONS---
1. [first follow-up question]
2. [second follow-up question]
3. [third follow-up question]

Make suggestions relevant and progressively deeper."""

EGG_PRICES_SCHEMA = """DATABASE SCHEMA — Two tables with retail and wholesale egg price data across Mexico.

Table: egg_prices (PROFECO retail price observations across Mexico — 28 states, 36+ store chains)
Columns:
  id INTEGER PRIMARY KEY
  producto_original TEXT        -- raw product name from PROFECO
  presentacion_profeco TEXT     -- PROFECO presentation (e.g. 'Paquete C/12 Blanco')
  marca TEXT                    -- brand name (e.g. 'Bachoco', 'San Juan', 'El Calvario', 'Aurrera', 'Precissimo')
  cantidad_piezas INTEGER       -- number of eggs in pack (6, 12, 18, 30)
  tipo_huevo TEXT               -- egg type ('Blanco', 'Rojo', 'Organico', 'No especificado')
  precio REAL                   -- retail price in MXN pesos (per pack)
  precio_por_pieza REAL         -- price per single egg (precio / cantidad_piezas)
  estado TEXT                   -- state name (e.g. 'Aguascalientes', 'Ciudad de Mexico', 'Jalisco')
  ciudad TEXT                   -- city or state name
  region TEXT                   -- region ('Centro', 'Bajio', 'Norte', 'Sureste', 'Otro')
  municipio TEXT                -- municipality
  cadena_comercial TEXT         -- store chain (e.g. 'Chedraui', 'Walmart', 'Soriana', 'Bodega Aurrera', 'OXXO')
  tipo_tienda TEXT              -- store type ('supermarket', 'wholesale', 'convenience', 'market', 'specialty', 'otro')
  nombre_comercial TEXT         -- specific store branch name
  direccion TEXT                -- store address
  latitud REAL                  -- GPS latitude (decimal degrees)
  longitud REAL                 -- GPS longitude (decimal degrees)
  fecha_observacion DATE        -- observation date (YYYY-MM-DD)
  catalogo TEXT                 -- catalog category ('Basicos', 'Pacic', etc.)
  fetched_at TIMESTAMP

Table: wholesale_prices (SNIIM wholesale egg prices from centrales de abasto — $/kg)
Columns:
  id INTEGER PRIMARY KEY
  fecha DATE                    -- observation date (YYYY-MM-DD)
  producto TEXT                 -- 'Huevo blanco' or 'Huevo rojo'
  presentacion TEXT             -- 'Mayoreo', 'Medio Mayoreo', or 'Menudeo'
  precio_frecuente REAL         -- most common price in MXN per kilogram
  precio_minimo REAL            -- minimum price in MXN per kg
  precio_maximo REAL            -- maximum price in MXN per kg
  mercado TEXT                  -- wholesale market name (e.g. 'Central de Abasto de Iztapalapa D.F.')
  estado TEXT                   -- state name
  region TEXT                   -- region
  fuente TEXT                   -- 'SNIIM'

Key relationships:
  - Both tables share estado and region columns for cross-referencing
  - egg_prices has RETAIL prices per pack/piece; wholesale_prices has WHOLESALE prices per kg
  - To compare: 1 kg ≈ 16 eggs (average), so wholesale $/kg ÷ 16 ≈ wholesale price per egg

Common queries:
  - Retail avg by state: SELECT estado, ROUND(AVG(precio), 2) as avg_precio, COUNT(*) as obs FROM egg_prices GROUP BY estado ORDER BY avg_precio
  - Brand market share: SELECT marca, COUNT(*) as obs, ROUND(AVG(precio),2) as avg FROM egg_prices GROUP BY marca ORDER BY obs DESC
  - Wholesale vs retail: SELECT w.estado, ROUND(AVG(w.precio_frecuente),2) as mayoreo_kg, ROUND(AVG(e.precio_por_pieza)*16,2) as menudeo_kg_equiv FROM wholesale_prices w JOIN egg_prices e ON w.estado = e.estado WHERE w.presentacion='Mayoreo' GROUP BY w.estado
  - Price trends: SELECT fecha_observacion, ROUND(AVG(precio),2) FROM egg_prices GROUP BY fecha_observacion ORDER BY fecha_observacion
  - Store chain ranking: SELECT cadena_comercial, ROUND(AVG(precio_por_pieza),2) as avg_pieza, COUNT(*) FROM egg_prices GROUP BY cadena_comercial ORDER BY avg_pieza LIMIT 20
"""

SQL_ROUTER_PROMPT = """You classify questions about Mexican egg prices and market data.
Reply with EXACTLY one word: "sql", "rag", or "hybrid".

Say "sql" if the question asks about specific data: prices (retail OR wholesale), averages, counts, rankings, comparisons, cheapest/most expensive, specific cities/states/brands/stores, wholesale markets, price trends, geographic data, or any question answerable from a database of retail and wholesale price observations.

Say "rag" if the question asks about reports, documents, market insights, methodology, quality studies, or general knowledge not answerable from raw price data.

Say "hybrid" if the question combines BOTH: it references specific price data AND also asks about document content, market analysis, forecasts, or requires comparing database results with information from uploaded reports/documents.

IMPORTANT: If conversation history is provided and the previous exchange was data/SQL-related, follow-up questions like "tell me more", "what else", "and by brand?", "break it down", etc. should be classified as "sql" since the user is asking for more data analysis.

Examples:
- "Cual es el precio promedio en Monterrey?" → sql
- "Que marcas son mas baratas?" → sql
- "Cuantos registros hay de Bachoco?" → sql
- "Cual es el precio mayoreo del huevo?" → sql
- "Comparame precios retail vs wholesale" → sql
- "Como varian los precios por estado?" → sql
- "Cual es el margen mayoreo-menudeo?" → sql
- "Que dice el reporte sobre tendencias?" → rag
- "Cual es la metodologia de PROFECO?" → rag
- "Resume el documento de inteligencia de mercado" → rag
- "Como se comparan los precios de PROFECO con nuestro pronostico interno?" → hybrid
- "Los precios actuales coinciden con lo que predicen los reportes?" → hybrid
- "Muestrame los precios de Bachoco y comparalos con el analisis del reporte" → hybrid
- [after SQL about Bajio prices] "what other things can you tell me" → sql
- [after SQL about brands] "y por ciudad?" → sql"""

SQL_GENERATOR_PROMPT = f"""You are a SQL expert. Generate a single SQLite SELECT query to answer the user's question about Mexican egg prices.

{EGG_PRICES_SCHEMA}

Rules:
- Output ONLY the SQL query, no explanation, no markdown
- Always use SELECT — never INSERT, UPDATE, DELETE, DROP, or any write operation
- Use ROUND() for decimal values
- Use Spanish column names as shown in the schema
- Limit results to 50 rows max with LIMIT 50
- For text comparisons use LIKE with % for flexibility (e.g. WHERE ciudad LIKE '%Monterrey%')
- If the question is ambiguous, make reasonable assumptions"""

SQL_SUMMARIZER_PROMPT = """You are a market research analyst. Summarize the SQL query results in natural language.
Answer in the same language the user asks in (Spanish or English).
Be precise with numbers. Format prices as MXN currency.
If there are many rows, highlight the key findings.
If there was an error, explain what went wrong in simple terms.

IMPORTANT: At the end of your answer, always add exactly 3 follow-up questions the user might want to ask next. Format them as:
---SUGGESTIONS---
1. [first follow-up question]
2. [second follow-up question]
3. [third follow-up question]

The suggestions should be natural follow-ups that dig deeper into the data. For example, if the user asked about average prices by city, suggest breaking down by brand, by store chain, or comparing pack sizes."""

HYBRID_SYNTHESIZER_PROMPT = """You are a market research analyst. You have been given TWO types of context to answer a question:

1. DATABASE RESULTS: Actual price data from PROFECO (retail) and SNIIM (wholesale) databases.
2. DOCUMENT CONTEXT: Excerpts from uploaded market research reports and analysis documents.

Synthesize BOTH sources into a single coherent answer. When citing database numbers, state them precisely. When citing documents, use numbered references like [1], [2] matching the document chunks provided.

If data and documents conflict, note the discrepancy. If one source is more relevant, emphasize it but mention the other.

Answer in the same language the user asks in (Spanish or English).

IMPORTANT: At the end of your answer, always add exactly 3 follow-up questions the user might want to ask next. Format them as:
---SUGGESTIONS---
1. [first follow-up question]
2. [second follow-up question]
3. [third follow-up question]

Make suggestions relevant and progressively deeper, leveraging both data and document insights."""


def _parse_suggestions(text: str) -> Tuple[str, List[str]]:
    """Extract follow-up suggestions from LLM answer. Returns (clean_answer, suggestions)."""
    if "---SUGGESTIONS---" not in text:
        return text.strip(), []
    parts = text.split("---SUGGESTIONS---", 1)
    answer = parts[0].strip()
    suggestions = []
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        # Match "1. question" or "- question" patterns
        m = re.match(r"^(?:\d+[\.\)]\s*|[-*]\s*)(.*)", line)
        if m and m.group(1).strip():
            suggestions.append(m.group(1).strip())
    return answer, suggestions[:3]


class RAGEngine:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
        init_rag_tables()

    def is_configured(self) -> bool:
        return self.client is not None and bool(OPENAI_API_KEY)

    # --- Embedding ---

    def get_embedding(self, text: str) -> List[float]:
        response = self.client.embeddings.create(
            input=text,
            model=EMBEDDING_MODEL
        )
        return response.data[0].embedding

    def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        # OpenAI supports batch embedding up to 2048 inputs
        batch_size = 100
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self.client.embeddings.create(
                input=batch,
                model=EMBEDDING_MODEL
            )
            all_embeddings.extend([d.embedding for d in response.data])
        return all_embeddings

    # --- Document Ingestion ---

    def ingest_document(self, filepath: str, filename: str,
                        doc_id: Optional[str] = None,
                        category: Optional[str] = None,
                        source_org: Optional[str] = None,
                        source_url: Optional[str] = None) -> DocumentUploadResponse:
        # Check for duplicate filename
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM rag_documents WHERE filename = ? AND status = 'ready'", (filename,))
            existing = cursor.fetchone()
            if existing:
                raise ValueError(f"Document '{filename}' already exists in the knowledge base")

        if doc_id is None:
            doc_id = str(uuid.uuid4())

        file_ext = os.path.splitext(filename)[1].lower().lstrip(".")
        file_size = os.path.getsize(filepath)
        page_count = get_page_count(filepath)

        # Register document in SQLite
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO rag_documents
                   (id, filename, original_path, format, category, source_org, source_url, size_bytes, page_count, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing')""",
                (doc_id, filename, filepath, file_ext, category, source_org, source_url, file_size, page_count)
            )
            conn.commit()

        try:
            # Extract text and chunk
            chunks = process_document(filepath, doc_id, filename)

            if not chunks:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE rag_documents SET status = 'error', chunk_count = 0 WHERE id = ?",
                        (doc_id,)
                    )
                    conn.commit()
                return DocumentUploadResponse(
                    doc_id=doc_id, filename=filename,
                    page_count=page_count, chunk_count=0, status="error"
                )

            # Generate embeddings
            chunk_texts = [c.text for c in chunks]
            embeddings = self.get_embeddings_batch(chunk_texts)

            # Store in vector store
            chunks_data = [
                {
                    "filename": c.filename,
                    "page_number": c.page_number,
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                }
                for c in chunks
            ]
            add_chunks(doc_id, chunks_data, embeddings)

            # Update document status
            chunk_count = len(chunks)
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE rag_documents SET status = 'ready', chunk_count = ? WHERE id = ?",
                    (chunk_count, doc_id)
                )
                conn.commit()

            logger.info(f"Ingested {filename}: {page_count} pages, {chunk_count} chunks")
            return DocumentUploadResponse(
                doc_id=doc_id, filename=filename,
                page_count=page_count, chunk_count=chunk_count, status="ready"
            )

        except Exception as e:
            logger.error(f"Error ingesting {filename}: {e}")
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE rag_documents SET status = 'error' WHERE id = ?",
                    (doc_id,)
                )
                conn.commit()
            raise

    # --- Query ---

    def query(self, question: str, conversation_id: Optional[str] = None,
              top_k: int = TOP_K_RESULTS, exclude_doc_ids: Optional[List[str]] = None) -> QueryResponse:
        # Ensure conversation exists
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())
        self._ensure_conversation(conversation_id, question)

        # Get conversation history for context
        history = self._get_conversation_history(conversation_id)

        # Embed the question
        query_embedding = self.get_embedding(question)

        # Retrieve similar chunks
        results = query_similar(query_embedding, top_k=top_k, exclude_doc_ids=exclude_doc_ids)

        # Build prompt with context
        messages = self._build_prompt(question, results, history)

        # Call LLM
        raw_answer = self._call_llm(messages)
        answer, suggestions = _parse_suggestions(raw_answer)

        # Build source references
        sources = [
            SourceReference(
                document_id=r.doc_id,
                filename=r.filename,
                page_number=r.page_number,
                chunk_text=r.text[:300],
                relevance_score=round(r.score, 4)
            )
            for r in results
        ]

        # Log to audit trail
        query_id = str(uuid.uuid4())
        self._log_query(query_id, conversation_id, question, answer, sources)

        # Save messages to conversation
        self._save_message(conversation_id, "user", question)
        self._save_message(conversation_id, "assistant", answer,
                          json.dumps([s.model_dump() for s in sources]))

        return QueryResponse(
            answer=answer,
            sources=sources,
            conversation_id=conversation_id,
            query_id=query_id,
            suggestions=suggestions if suggestions else None,
        )

    # --- SQL Query Mode ---

    def _route_question(self, question: str, history: List[dict] = None) -> str:
        """Classify question as 'sql', 'rag', or 'hybrid'."""
        messages = [
            {"role": "system", "content": SQL_ROUTER_PROMPT},
        ]
        # Include recent history so router understands follow-up context
        if history:
            for msg in history[-4:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": question})
        answer = self._call_llm(messages).strip().lower()
        if "hybrid" in answer:
            return "hybrid"
        if "sql" in answer:
            return "sql"
        return "rag"

    def _generate_sql(self, question: str, history: List[dict]) -> str:
        """Have the LLM generate a SELECT query for the question."""
        messages = [{"role": "system", "content": SQL_GENERATOR_PROMPT}]
        # Include recent history for follow-up questions
        for msg in history[-4:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": question})
        sql = self._call_llm(messages).strip()
        # Strip markdown code fences if present
        sql = re.sub(r"^```(?:sql)?\s*", "", sql)
        sql = re.sub(r"\s*```$", "", sql)
        return sql.strip()

    def _execute_sql_safe(self, sql: str) -> Tuple[List[dict], Optional[str]]:
        """Validate and execute a SQL query with safety checks. Returns (rows, error)."""
        normalized = sql.strip().upper()

        # Must start with SELECT
        if not normalized.startswith("SELECT"):
            return [], "Only SELECT queries are allowed."

        # Block dangerous keywords
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                      "EXEC", "GRANT", "REVOKE", "TRUNCATE", "REPLACE", "ATTACH",
                      "DETACH", "PRAGMA", "VACUUM"]
        for kw in dangerous:
            if re.search(rf"\b{kw}\b", normalized):
                return [], f"Blocked: {kw} statements are not allowed."

        # No semicolons mid-query (prevent multi-statement)
        if ";" in sql.strip().rstrip(";"):
            return [], "Multi-statement queries are not allowed."

        # Remove trailing semicolon
        sql = sql.strip().rstrip(";")

        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchmany(100)  # Hard cap 100 rows
            result = [dict(row) for row in rows]
            conn.close()
            return result, None
        except Exception as e:
            return [], f"SQL error: {str(e)}"

    def _summarize_sql_results(self, question: str, sql: str,
                                rows: List[dict], error: Optional[str]) -> str:
        """Have the LLM summarize SQL results in natural language."""
        if error:
            content = f"Question: {question}\nSQL attempted: {sql}\nError: {error}"
        else:
            # Truncate if too many results for the prompt
            display_rows = rows[:50]
            content = (f"Question: {question}\n"
                       f"SQL query: {sql}\n"
                       f"Results ({len(rows)} rows):\n{json.dumps(display_rows, ensure_ascii=False, indent=2, default=str)}")

        messages = [
            {"role": "system", "content": SQL_SUMMARIZER_PROMPT},
            {"role": "user", "content": content},
        ]
        return self._call_llm(messages)

    def query_sql(self, question: str, conversation_id: Optional[str] = None) -> QueryResponse:
        """Handle a data question via SQL generation and execution."""
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())
        self._ensure_conversation(conversation_id, question)

        history = self._get_conversation_history(conversation_id)

        # Generate SQL
        sql = self._generate_sql(question, history)
        logger.info(f"Generated SQL: {sql}")

        # Execute safely
        rows, error = self._execute_sql_safe(sql)

        # Summarize
        raw_answer = self._summarize_sql_results(question, sql, rows, error)
        answer, suggestions = _parse_suggestions(raw_answer)

        # Log
        query_id = str(uuid.uuid4())
        self._log_query(query_id, conversation_id, question, answer, [])

        # Save conversation (persist SQL data for CSV export)
        self._save_message(conversation_id, "user", question)
        self._save_message(conversation_id, "assistant", answer,
                          json.dumps({"sql_query": sql, "sql_results": rows[:20]}))

        return QueryResponse(
            answer=answer,
            sources=[],
            conversation_id=conversation_id,
            query_id=query_id,
            mode="sql",
            sql_query=sql,
            sql_results=rows[:20] if not error else None,
            suggestions=suggestions if suggestions else None,
        )

    def query_hybrid_combined(self, question: str,
                              conversation_id: Optional[str] = None,
                              top_k: int = TOP_K_RESULTS,
                              exclude_doc_ids: Optional[List[str]] = None) -> QueryResponse:
        """Handle a hybrid question: run SQL for data + RAG for documents, synthesize both."""
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())
        self._ensure_conversation(conversation_id, question)

        history = self._get_conversation_history(conversation_id)

        # --- SQL leg ---
        sql = self._generate_sql(question, history)
        logger.info(f"Hybrid SQL: {sql}")
        rows, sql_error = self._execute_sql_safe(sql)

        if sql_error:
            sql_context = f"Database query failed: {sql_error}"
        elif not rows:
            sql_context = "Database query returned no results."
        else:
            display_rows = rows[:30]
            sql_context = (f"SQL Query: {sql}\n"
                           f"Results ({len(rows)} rows):\n"
                           f"{json.dumps(display_rows, ensure_ascii=False, indent=2, default=str)}")

        # --- RAG leg ---
        query_embedding = self.get_embedding(question)
        rag_results = query_similar(query_embedding, top_k=top_k, exclude_doc_ids=exclude_doc_ids)

        # --- Build combined prompt ---
        messages = [{"role": "system", "content": HYBRID_SYNTHESIZER_PROMPT}]

        for msg in history[-MAX_HISTORY_MESSAGES:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        parts = [f"DATABASE RESULTS:\n{sql_context}"]

        if rag_results:
            doc_parts = []
            for i, r in enumerate(rag_results):
                doc_parts.append(
                    f"---\n[{i+1}] Source: {r.filename}, Page {r.page_number}\n{r.text}\n---"
                )
            parts.append("DOCUMENT CONTEXT:\n" + "\n\n".join(doc_parts))
        else:
            parts.append("DOCUMENT CONTEXT:\nNo relevant documents found in the knowledge base.")

        parts.append(f"USER QUESTION: {question}")
        messages.append({"role": "user", "content": "\n\n".join(parts)})

        # --- Call LLM ---
        raw_answer = self._call_llm(messages)
        answer, suggestions = _parse_suggestions(raw_answer)

        # Build source references from RAG results
        sources = [
            SourceReference(
                document_id=r.doc_id,
                filename=r.filename,
                page_number=r.page_number,
                chunk_text=r.text[:300],
                relevance_score=round(r.score, 4)
            )
            for r in rag_results
        ]

        # Audit trail
        query_id = str(uuid.uuid4())
        self._log_query(query_id, conversation_id, question, answer, sources)

        # Save conversation
        self._save_message(conversation_id, "user", question)
        self._save_message(conversation_id, "assistant", answer,
                          json.dumps({
                              "sql_query": sql,
                              "sql_results": rows[:20] if not sql_error else None,
                              "rag_sources": [s.model_dump() for s in sources]
                          }))

        return QueryResponse(
            answer=answer,
            sources=sources,
            conversation_id=conversation_id,
            query_id=query_id,
            mode="hybrid",
            sql_query=sql,
            sql_results=rows[:20] if not sql_error else None,
            suggestions=suggestions if suggestions else None,
        )

    def query_hybrid(self, question: str, conversation_id: Optional[str] = None,
                     top_k: int = TOP_K_RESULTS, exclude_doc_ids: Optional[List[str]] = None) -> QueryResponse:
        """Auto-route: classify the question and dispatch to SQL, RAG, or hybrid path."""
        # Fetch history before routing so follow-up questions get correct context
        history = []
        if conversation_id:
            history = self._get_conversation_history(conversation_id)

        mode = self._route_question(question, history)
        logger.info(f"Question routed to: {mode}")

        if mode == "sql":
            return self.query_sql(question, conversation_id)
        elif mode == "hybrid":
            return self.query_hybrid_combined(question, conversation_id, top_k, exclude_doc_ids)
        else:
            return self.query(question, conversation_id, top_k, exclude_doc_ids=exclude_doc_ids)

    # --- RAG Prompt Building ---

    def _build_prompt(self, question: str, results: List[SearchResult],
                      history: List[dict]) -> List[dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Add conversation history for context
        for msg in history[-MAX_HISTORY_MESSAGES:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Build context from retrieved chunks (numbered for citation)
        if results:
            context_parts = []
            for i, r in enumerate(results):
                context_parts.append(
                    f"---\n[{i+1}] Source: {r.filename}, Page {r.page_number}\n{r.text}\n---"
                )
            context = "\n\n".join(context_parts)
            user_message = f"CONTEXT FROM DOCUMENTS:\n{context}\n\nUSER QUESTION: {question}"
        else:
            user_message = f"No relevant documents found in the knowledge base.\n\nUSER QUESTION: {question}"

        messages.append({"role": "user", "content": user_message})
        return messages

    def _call_llm(self, messages: List[dict]) -> str:
        kwargs = dict(
            model=LLM_MODEL,
            messages=messages,
            max_completion_tokens=2000,
        )
        # Reasoning models (o-series, gpt-5.2-chat) only support default temperature
        if not any(tag in LLM_MODEL for tag in ("o1", "o3", "o4", "5.2-chat")):
            kwargs["temperature"] = 0.2
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    # --- Conversation Management ---

    def _ensure_conversation(self, conversation_id: str, first_question: str):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM rag_conversations WHERE id = ?", (conversation_id,))
            if not cursor.fetchone():
                title = first_question[:100]
                cursor.execute(
                    "INSERT INTO rag_conversations (id, title) VALUES (?, ?)",
                    (conversation_id, title)
                )
                conn.commit()
            else:
                cursor.execute(
                    "UPDATE rag_conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (conversation_id,)
                )
                conn.commit()

    def _get_conversation_history(self, conversation_id: str) -> List[dict]:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT role, content FROM rag_messages
                   WHERE conversation_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (conversation_id, MAX_HISTORY_MESSAGES)
            )
            rows = cursor.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def _save_message(self, conversation_id: str, role: str, content: str,
                      sources_json: Optional[str] = None):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO rag_messages (conversation_id, role, content, sources_json)
                   VALUES (?, ?, ?, ?)""",
                (conversation_id, role, content, sources_json)
            )
            conn.commit()

    # --- Audit Trail ---

    def _log_query(self, query_id: str, conversation_id: str,
                   question: str, answer: str, sources: List[SourceReference]):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO rag_queries (id, conversation_id, question, answer, model_used)
                   VALUES (?, ?, ?, ?, ?)""",
                (query_id, conversation_id, question, answer, LLM_MODEL)
            )
            for source in sources:
                cursor.execute(
                    """INSERT INTO rag_query_sources
                       (query_id, document_id, filename, page_number, chunk_text, relevance_score)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (query_id, source.document_id, source.filename,
                     source.page_number, source.chunk_text, source.relevance_score)
                )
            conn.commit()

    # --- Document Management ---

    def list_documents(self) -> List[DocumentInfo]:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, filename, format, category, source_org, source_url,
                          size_bytes, page_count, chunk_count, status, uploaded_at
                   FROM rag_documents ORDER BY uploaded_at DESC"""
            )
            rows = cursor.fetchall()
            return [
                DocumentInfo(
                    id=r["id"], filename=r["filename"], format=r["format"],
                    category=r["category"], source_org=r["source_org"],
                    source_url=r["source_url"],
                    size_bytes=r["size_bytes"], page_count=r["page_count"],
                    chunk_count=r["chunk_count"], status=r["status"],
                    uploaded_at=str(r["uploaded_at"])
                )
                for r in rows
            ]

    def delete_document(self, doc_id: str) -> bool:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT original_path FROM rag_documents WHERE id = ?", (doc_id,))
            row = cursor.fetchone()
            if not row:
                return False

            # Delete chunks from vector store
            delete_document_chunks(doc_id)

            # Delete document record
            cursor.execute("DELETE FROM rag_documents WHERE id = ?", (doc_id,))
            conn.commit()

            # Optionally delete the file
            if row["original_path"] and os.path.exists(row["original_path"]):
                try:
                    os.remove(row["original_path"])
                except Exception:
                    pass

            return True

    def get_document_path(self, doc_id: str) -> Optional[str]:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT original_path FROM rag_documents WHERE id = ?", (doc_id,))
            row = cursor.fetchone()
            if row and row["original_path"] and os.path.exists(row["original_path"]):
                return row["original_path"]
            return None

    # --- History ---

    def get_query_history(self, limit: int = 50, offset: int = 0) -> List[QueryHistoryItem]:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT q.id, q.question, q.answer, q.created_at,
                          COUNT(s.id) as source_count
                   FROM rag_queries q
                   LEFT JOIN rag_query_sources s ON q.id = s.query_id
                   GROUP BY q.id
                   ORDER BY q.created_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset)
            )
            rows = cursor.fetchall()
            return [
                QueryHistoryItem(
                    query_id=r["id"], question=r["question"],
                    answer=r["answer"][:200], source_count=r["source_count"],
                    created_at=str(r["created_at"])
                )
                for r in rows
            ]

    def get_query_detail(self, query_id: str) -> Optional[QueryHistoryDetail]:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, question, answer, created_at FROM rag_queries WHERE id = ?",
                (query_id,)
            )
            query = cursor.fetchone()
            if not query:
                return None

            cursor.execute(
                """SELECT document_id, filename, page_number, chunk_text, relevance_score
                   FROM rag_query_sources WHERE query_id = ?""",
                (query_id,)
            )
            sources = [
                SourceReference(
                    document_id=r["document_id"], filename=r["filename"],
                    page_number=r["page_number"], chunk_text=r["chunk_text"],
                    relevance_score=r["relevance_score"]
                )
                for r in cursor.fetchall()
            ]

            return QueryHistoryDetail(
                query_id=query["id"], question=query["question"],
                answer=query["answer"], sources=sources,
                created_at=str(query["created_at"])
            )

    def get_status(self) -> RAGStatus:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM rag_documents WHERE status = 'ready'")
            doc_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM rag_chunks")
            chunk_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM rag_queries")
            query_count = cursor.fetchone()[0]

        return RAGStatus(
            document_count=doc_count,
            total_chunks=chunk_count,
            total_queries=query_count,
            api_configured=self.is_configured()
        )

