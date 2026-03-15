import asyncio
from supabase import create_client, Client
from supabase.client import ClientOptions
from app.config import settings
from typing import Optional, Dict, Any

class SupabaseClient:
    def __init__(self):
        self._client = None
        self._lock = asyncio.Lock()

    async def get_client(self):
        async with self._lock:
            if self._client is None:
                print("[Supabase] 🔄 Initializing Async Client...", flush=True)
                start = asyncio.get_event_loop().time()
                from supabase import create_async_client
                from supabase.client import ClientOptions
                self._client = await create_async_client(
                    settings.SUPABASE_URL, 
                    settings.SUPABASE_SERVICE_KEY,
                    options=ClientOptions(postgrest_client_timeout=20)
                )
                end = asyncio.get_event_loop().time()
                print(f"[Supabase] ✅ Async Client ready in {end-start:.2f}s", flush=True)
            return self._client

    async def create_lead(self, name: str, phone: str, company: str) -> Dict[str, Any]:
        """Inserts into leads table."""
        client = await self.get_client()
        result = await client.table("leads").insert({
            "name": name,
            "phone": phone,
            "company": company,
            "status": "new"
        }).execute()
        return result.data[0] if result.data else {}

    async def update_lead_status(self, phone: str, status: str) -> Dict[str, Any]:
        """Updates status field."""
        client = await self.get_client()
        result = await client.table("leads").update({
            "status": status
        }).eq("phone", phone).execute()
        return result.data[0] if result.data else {}

    async def log_message(self, phone: str, direction: str, body: str, state: str, source: str = "text") -> Dict[str, Any]:
        """Inserts into messages table."""
        lead = await self.get_lead_by_phone(phone)
        lead_id = lead.get("id") if lead else None
        client = await self.get_client()

        result = await client.table("messages").insert({
            "lead_id": lead_id,
            "direction": direction,
            "content": body,
            "state": state,
            "source": source
        }).execute()
        return result.data[0] if result.data else {}

    async def get_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Returns lead record."""
        client = await self.get_client()
        result = await client.table("leads").select("*").eq("phone", phone).execute()
        return result.data[0] if result.data else None

supabase_client = SupabaseClient()
