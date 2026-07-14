"""
scripts/sync_prompt.py
======================
Sync prompts/system_prompt.txt -> a client's `system_prompt` field in Supabase.

The DB is the live source of truth (per ADR 0001), and prompts/system_prompt.txt
is the git-tracked source. This script pushes the file into the DB so the two
never drift. Run it whenever you edit the prompt file.

Usage:
    python scripts/sync_prompt.py                 # default client
    python scripts/sync_prompt.py <client_id>     # specific client
"""

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from app.config import settings

DEFAULT_CLIENT_ID = "eb89a504-7a6d-453f-89cd-3c95ed2a22f1"  # Markeye AI
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "system_prompt.txt")


def main() -> int:
    client_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CLIENT_ID

    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        prompt = f.read()

    print(f"Syncing prompts/system_prompt.txt ({len(prompt)} chars) -> client {client_id}")

    resp = httpx.patch(
        f"{settings.SUPABASE_URL}/rest/v1/clients?id=eq.{client_id}",
        headers={
            "apikey": settings.SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json={"system_prompt": prompt},
        timeout=30,
    )

    if resp.status_code in (200, 204):
        print(f"OK: synced ({resp.status_code}). Live within the client-config cache TTL (~5 min).")
        return 0
    print(f"FAILED: {resp.status_code} {resp.text[:300]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
