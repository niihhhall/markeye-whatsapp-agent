import asyncio
from supabase import create_client, Client
from supabase.client import ClientOptions
from app.config import settings
from typing import Optional, Dict, Any

import logging
logger = logging.getLogger(__name__)

class SupabaseClient:
    def __init__(self):
        self._client = None
        self._lock = asyncio.Lock()

    async def get_client(self):
        async with self._lock:
            if self._client is None:
                logger.info("[Supabase] Initializing Async Client...")
                start = asyncio.get_event_loop().time()
                from supabase import create_async_client
                from supabase.client import ClientOptions
                self._client = await create_async_client(
                    settings.SUPABASE_URL, 
                    settings.SUPABASE_SERVICE_KEY,
                    options=ClientOptions(postgrest_client_timeout=20)
                )
                end = asyncio.get_event_loop().time()
                logger.info(f"[Supabase] OK: Async Client ready in {end-start:.2f}s")
            return self._client

supabase_client = SupabaseClient()
