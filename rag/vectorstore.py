"""SQLite + numpy vector store for document chunk embeddings.

Stores embeddings as numpy binary blobs in SQLite. Performs cosine similarity
search in Python using numpy. Simple, zero-dependency (beyond numpy), and
compatible with any Python version.
"""

import sqlite3
import json
import logging
from contextlib import contextmanager
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

import numpy as np

from rag.config import DB_PATH, EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    doc_id: str
    filename: str
    page_number: int
    chunk_index: int
    text: str
    score: float


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_rag_tables():
    """Create all RAG-related tables if they don't exist."""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                original_path TEXT,
                format TEXT NOT NULL,
                category TEXT,
                source_org TEXT,
                source_url TEXT,
                size_bytes INTEGER,
                page_count INTEGER,
                chunk_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'processing',
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: add source_url column if table already existed without it
        try:
            cursor.execute("ALTER TABLE rag_documents ADD COLUMN source_url TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                page_number INTEGER,
                chunk_index INTEGER,
                text TEXT NOT NULL,
                embedding BLOB,
                FOREIGN KEY (doc_id) REFERENCES rag_documents(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_queries (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                model_used TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_query_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                filename TEXT,
                page_number INTEGER,
                chunk_text TEXT,
                relevance_score REAL,
                FOREIGN KEY (query_id) REFERENCES rag_queries(id),
                FOREIGN KEY (document_id) REFERENCES rag_documents(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES rag_conversations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                rating TEXT NOT NULL CHECK(rating IN ('up', 'down')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (query_id) REFERENCES rag_queries(id)
            )
        """)

        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks(doc_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_queries_date ON rag_queries(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_sources_query ON rag_query_sources(query_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_messages_conv ON rag_messages(conversation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_conversations_date ON rag_conversations(updated_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_feedback_query ON rag_feedback(query_id)")

        conn.commit()
        logger.info("RAG tables initialized")


def add_chunks(doc_id: str, chunks_data: List[Dict[str, Any]], embeddings: List[List[float]]):
    """Store chunks with their embeddings in SQLite."""
    with get_db() as conn:
        cursor = conn.cursor()
        for chunk, embedding in zip(chunks_data, embeddings):
            emb_blob = np.array(embedding, dtype=np.float32).tobytes()
            cursor.execute(
                """INSERT INTO rag_chunks (doc_id, filename, page_number, chunk_index, text, embedding)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (doc_id, chunk["filename"], chunk["page_number"],
                 chunk["chunk_index"], chunk["text"], emb_blob)
            )
        conn.commit()
        logger.info(f"Stored {len(chunks_data)} chunks for document {doc_id}")


def query_similar(query_embedding: List[float], top_k: int = 5,
                   exclude_doc_ids: Optional[List[str]] = None) -> List[SearchResult]:
    """Find the most similar chunks using cosine similarity."""
    query_vec = np.array(query_embedding, dtype=np.float32)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []

    with get_db() as conn:
        cursor = conn.cursor()
        if exclude_doc_ids:
            placeholders = ','.join(['?'] * len(exclude_doc_ids))
            cursor.execute(
                f"SELECT doc_id, filename, page_number, chunk_index, text, embedding FROM rag_chunks WHERE doc_id NOT IN ({placeholders})",
                exclude_doc_ids
            )
        else:
            cursor.execute("SELECT doc_id, filename, page_number, chunk_index, text, embedding FROM rag_chunks")
        rows = cursor.fetchall()

    if not rows:
        return []

    results = []
    for row in rows:
        emb = np.frombuffer(row["embedding"], dtype=np.float32)
        emb_norm = np.linalg.norm(emb)
        if emb_norm == 0:
            continue
        score = float(np.dot(query_vec, emb) / (query_norm * emb_norm))
        results.append(SearchResult(
            doc_id=row["doc_id"],
            filename=row["filename"],
            page_number=row["page_number"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            score=score
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:top_k]


def delete_document_chunks(doc_id: str):
    """Remove all chunks for a document."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rag_chunks WHERE doc_id = ?", (doc_id,))
        conn.commit()
        logger.info(f"Deleted chunks for document {doc_id}")


def get_chunk_count() -> int:
    """Get total number of chunks in the store."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM rag_chunks")
        return cursor.fetchone()[0]


def get_document_chunk_count(doc_id: str) -> int:
    """Get number of chunks for a specific document."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM rag_chunks WHERE doc_id = ?", (doc_id,))
        return cursor.fetchone()[0]
