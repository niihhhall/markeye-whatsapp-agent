"""
app/knowledge.py
================
Factual grounding retrieval (ADR 0004 Workstream A).

Two backends behind one `retrieve_knowledge(query)` entry point:

  1. **Pinecone (Option B, integrated embeddings)** — when
     `USE_PINECONE_GROUNDING` is on and a key is configured. Uses Pinecone's
     server-side `llama-text-embed-v2`, so NO OpenAI is involved. This is the
     intended production path.
  2. **Legacy Supabase pgvector + OpenAI embeddings** — the pre-existing path.
     OpenAI embeddings are switched off, so this returns "" in practice; kept
     only as a documented fallback.

On ANY Pinecone error we return "" so `build_context` falls back cleanly to the
static `knowledge.md` layer (ADR 0001 Phase 2). Grounding can never block a reply.
"""

import asyncio
import logging
from typing import List
from app.config import settings

logger = logging.getLogger(__name__)

# ── Pinecone client (lazy singleton) ────────────────────────────────────────────
_pc = None
_pc_index = None


def _get_pinecone_index():
    """Lazily construct the Pinecone client + index handle. Sync (the SDK is
    sync); callers wrap it in asyncio.to_thread."""
    global _pc, _pc_index
    if _pc_index is not None:
        return _pc_index
    from pinecone import Pinecone
    _pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    _pc_index = _pc.Index(settings.PINECONE_INDEX_NAME)
    return _pc_index


def _pinecone_search_sync(query: str, limit: int) -> List[str]:
    """Blocking Pinecone search via integrated embeddings. Returns a list of
    matched text chunks (best-first)."""
    index = _get_pinecone_index()
    # The SDK has two generations of the search signature. Try the current
    # `query={...}` form first, then fall back to the `top_k=/inputs=` kwargs form.
    try:
        results = index.search(
            namespace=settings.PINECONE_NAMESPACE,
            query={"inputs": {"text": query}, "top_k": limit},
        )
    except TypeError:
        results = index.search(
            namespace=settings.PINECONE_NAMESPACE,
            top_k=limit,
            inputs={"text": query},
        )
    # SDK returns an object with .result.hits; be defensive about shape.
    hits = []
    try:
        hits = results.result.hits
    except AttributeError:
        # dict-style fallback
        hits = (results or {}).get("result", {}).get("hits", [])

    chunks: List[str] = []
    for hit in hits:
        fields = getattr(hit, "fields", None)
        if fields is None and isinstance(hit, dict):
            fields = hit.get("fields", {})
        fields = fields or {}
        text = fields.get("chunk_text") or fields.get("text") or ""
        if text:
            chunks.append(text.strip())
    return chunks


async def _retrieve_pinecone(query: str, limit: int) -> str:
    """Async wrapper around the sync Pinecone search."""
    chunks = await asyncio.to_thread(_pinecone_search_sync, query, limit)
    if not chunks:
        return ""
    return "\n\n".join(f"--- INFO SOURCE ---\n{c}" for c in chunks)


# ── Legacy Supabase pgvector path (OpenAI embeddings — currently disabled) ───────
async def _retrieve_supabase(query: str, threshold: float, limit: int) -> str:
    """Pre-existing pgvector path. Requires OpenAI embeddings, which are off,
    so this returns "" in practice. Kept as a documented fallback only."""
    if not settings.OPENAI_API_KEY:
        return ""
    try:
        from openai import AsyncOpenAI
        from app.supabase_client import supabase_client

        openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        clean = query.replace("\n", " ")
        emb = await openai_client.embeddings.create(
            input=[clean], model="text-embedding-3-small"
        )
        query_vec = emb.data[0].embedding

        client = await supabase_client.get_client()
        result = await client.rpc(
            "match_knowledge",
            {"query_embedding": query_vec, "match_threshold": threshold, "match_count": limit},
        ).execute()

        if not result.data:
            return ""
        parts = [
            f"--- INFO SOURCE ---\n{item.get('content', '').strip()}"
            for item in result.data
            if item.get("content", "").strip()
        ]
        return "\n\n".join(parts)
    except Exception as e:
        logger.error("[Knowledge][Supabase] %s", e)
        return ""


async def retrieve_knowledge(query: str, threshold: float = 0.4, limit: int = 3) -> str:
    """Retrieve grounding context for a query. Prefers Pinecone (Option B);
    falls back to Supabase; returns "" on any failure so build_context uses the
    static knowledge.md layer."""
    if settings.USE_PINECONE_GROUNDING and settings.PINECONE_API_KEY:
        try:
            ctx = await _retrieve_pinecone(query, limit)
            if ctx:
                return ctx
        except Exception as e:
            logger.error("[Knowledge][Pinecone] retrieval failed, falling back: %s", e)

    return await _retrieve_supabase(query, threshold, limit)
