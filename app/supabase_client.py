from supabase import create_client, Client
from supabase.client import ClientOptions
from app.config import settings
from typing import Optional, Dict, Any

class SupabaseClient:
    def __init__(self):
        # supabase-py v2 uses the same create_client but we should ensure we use it in an async context
        # Actually, if we want truly async we should use AsyncClient
        from supabase import AsyncClient, create_client
        self.client: AsyncClient = create_client(
            settings.SUPABASE_URL, 
            settings.SUPABASE_SERVICE_KEY,
            options=ClientOptions(postgrest_client_timeout=10)
        )

    async def create_lead(self, name: str, phone: str, company: str) -> Dict[str, Any]:
        """Inserts into leads table."""
        result = await self.client.table("leads").insert({
            "name": name,
            "phone": phone,
            "company": company,
            "status": "new"
        }).execute()
        return result.data[0] if result.data else {}

    async def update_lead_status(self, phone: str, status: str) -> Dict[str, Any]:
        """Updates status field."""
        result = await self.client.table("leads").update({
            "status": status
        }).eq("phone", phone).execute()
        return result.data[0] if result.data else {}

    async def log_message(self, phone: str, direction: str, body: str, state: str, source: str = "text") -> Dict[str, Any]:
        """Inserts into messages table."""
        lead = await self.get_lead_by_phone(phone)
        lead_id = lead.get("id") if lead else None

        result = await self.client.table("messages").insert({
            "lead_id": lead_id,
            "direction": direction,
            "content": body,
            "state": state,
            "source": source
        }).execute()
        return result.data[0] if result.data else {}

    async def get_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Returns lead record."""
        result = await self.client.table("leads").select("*").eq("phone", phone).execute()
        return result.data[0] if result.data else None

supabase_client = SupabaseClient()
