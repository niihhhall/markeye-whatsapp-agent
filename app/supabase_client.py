from supabase import create_client, Client
from app.config import settings
from typing import Optional, Dict, Any

class SupabaseClient:
    def __init__(self):
        self.client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

    async def create_lead(self, name: str, phone: str, company: str) -> Dict[str, Any]:
        """Inserts into leads table."""
        data, count = self.client.table("leads").insert({
            "name": name,
            "phone": phone,
            "company": company,
            "status": "new"
        }).execute()
        return data[1][0] if data[1] else {}

    async def update_lead_status(self, phone: str, status: str) -> Dict[str, Any]:
        """Updates status field."""
        data, count = self.client.table("leads").update({
            "status": status
        }).eq("phone", phone).execute()
        return data[1][0] if data[1] else {}

    async def log_message(self, phone: str, direction: str, body: str, state: str, source: str = "text") -> Dict[str, Any]:
        """Inserts into messages table."""
        # Fix: Resolve lead_id from phone and use 'content' column instead of 'body'
        lead = await self.get_lead_by_phone(phone)
        lead_id = lead.get("id") if lead else None

        data, count = self.client.table("messages").insert({
            "lead_id": lead_id,
            "direction": direction,
            "content": body,
            "state": state,
            "source": source
        }).execute()
        return data[1][0] if data[1] else {}

    async def get_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Returns lead record."""
        data, count = self.client.table("leads").select("*").eq("phone", phone).execute()
        return data[1][0] if data[1] else None

supabase_client = SupabaseClient()
