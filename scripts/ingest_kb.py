"""
scripts/ingest_kb.py
====================
ADR 0004 Workstream A — ingest Markeye's knowledge base into Pinecone with
integrated embeddings (llama-text-embed-v2, server-side — no OpenAI).

What it does:
  1. Creates the integrated index if it doesn't exist (create_index_for_model).
  2. Reads knowledge sources (prompts/layers/knowledge.md + prompts/knowledge/*.txt
     + prompts/objections/*.txt), chunks them, and upserts as records.
  3. Each record: {"_id", "chunk_text", "source"} — Pinecone embeds "chunk_text".

Run it locally or as a Heroku one-off dyno. Requires PINECONE_API_KEY.

    # Local (PowerShell):
    $env:PINECONE_API_KEY="..."; python scripts/ingest_kb.py

    # Heroku one-off:
    heroku run "python scripts/ingest_kb.py" -a mark-ai-sdr-agent

Re-run any time the knowledge base changes; upserts are idempotent by _id.
"""

import os
import sys
import glob
import time

# Allow running from repo root or scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Knowledge sources, in priority order.
SOURCES = [
    os.path.join(ROOT, "prompts", "layers", "knowledge.md"),
    *sorted(glob.glob(os.path.join(ROOT, "prompts", "knowledge", "*.txt"))),
    *sorted(glob.glob(os.path.join(ROOT, "prompts", "objections", "*.txt"))),
]

# Chunking: split on blank lines (paragraphs / sections), then pack up to ~900
# chars per chunk so each record is a coherent, self-contained fact block.
MAX_CHARS = 900


def chunk_text(text: str) -> list[str]:
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    chunks, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) + 2 <= MAX_CHARS:
            cur = f"{cur}\n\n{b}" if cur else b
        else:
            if cur:
                chunks.append(cur)
            # A single huge block: hard-split it.
            if len(b) > MAX_CHARS:
                for i in range(0, len(b), MAX_CHARS):
                    chunks.append(b[i:i + MAX_CHARS])
                cur = ""
            else:
                cur = b
    if cur:
        chunks.append(cur)
    return chunks


def build_records() -> list[dict]:
    records = []
    for path in SOURCES:
        if not os.path.exists(path):
            continue
        source = os.path.relpath(path, ROOT).replace("\\", "/")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for i, ch in enumerate(chunk_text(text)):
            rec_id = f"{source}#{i}".replace("/", "_").replace(".", "_")
            records.append({"_id": rec_id, "chunk_text": ch, "source": source})
    return records


def ensure_index(pc):
    name = settings.PINECONE_INDEX_NAME
    existing = [ix["name"] for ix in pc.list_indexes()]
    if name in existing:
        print(f"[ingest] Index '{name}' already exists.")
        return
    print(f"[ingest] Creating integrated index '{name}' ({settings.PINECONE_EMBED_MODEL})...")
    pc.create_index_for_model(
        name=name,
        cloud="aws",
        region="us-east-1",
        embed={
            "model": settings.PINECONE_EMBED_MODEL,
            "field_map": {"text": "chunk_text"},
        },
    )
    # Wait for readiness.
    for _ in range(30):
        desc = pc.describe_index(name)
        if desc.get("status", {}).get("ready"):
            break
        time.sleep(2)
    print(f"[ingest] Index '{name}' ready.")


def main():
    if not settings.PINECONE_API_KEY:
        print("ERROR: PINECONE_API_KEY not set. Set it and re-run.")
        sys.exit(1)

    from pinecone import Pinecone

    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    ensure_index(pc)
    index = pc.Index(settings.PINECONE_INDEX_NAME)

    records = build_records()
    if not records:
        print("ERROR: no knowledge sources found to ingest.")
        sys.exit(1)

    print(f"[ingest] Upserting {len(records)} chunks into namespace "
          f"'{settings.PINECONE_NAMESPACE}'...")
    # upsert_records in batches of 90 (server limit is ~96/req for integrated).
    batch = 90
    for i in range(0, len(records), batch):
        index.upsert_records(settings.PINECONE_NAMESPACE, records[i:i + batch])
        print(f"[ingest]   upserted {min(i + batch, len(records))}/{len(records)}")
    print("[ingest] Done. Allow ~10s for async embedding before querying.")


if __name__ == "__main__":
    main()
