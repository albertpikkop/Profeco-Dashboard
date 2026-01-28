"""Pydantic models for RAG API request/response schemas."""

from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime


class QueryRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    top_k: int = 5
    exclude_doc_ids: Optional[List[str]] = None


class SourceReference(BaseModel):
    document_id: str
    filename: str
    page_number: int
    chunk_text: str
    relevance_score: float


class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceReference]
    conversation_id: str
    query_id: str
    mode: str = "rag"
    sql_query: Optional[str] = None
    sql_results: Optional[List[dict]] = None
    suggestions: Optional[List[str]] = None


class DocumentInfo(BaseModel):
    id: str
    filename: str
    format: str
    category: Optional[str] = None
    source_org: Optional[str] = None
    source_url: Optional[str] = None
    size_bytes: int
    page_count: int
    chunk_count: int
    status: str
    uploaded_at: str


class DocumentUploadResponse(BaseModel):
    doc_id: str
    filename: str
    page_count: int
    chunk_count: int
    status: str


class QueryHistoryItem(BaseModel):
    query_id: str
    question: str
    answer: str
    source_count: int
    created_at: str


class QueryHistoryDetail(BaseModel):
    query_id: str
    question: str
    answer: str
    sources: List[SourceReference]
    created_at: str


class RAGStatus(BaseModel):
    document_count: int
    total_chunks: int
    total_queries: int
    api_configured: bool


class FeedbackRequest(BaseModel):
    query_id: str
    rating: str
