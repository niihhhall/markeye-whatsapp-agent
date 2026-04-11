import logging
import time
from typing import Optional, Dict, Any, List
from app.supabase_client import supabase_client

logger = logging.getLogger(__name__)

class ClientManager:
    def __init__(self):
        self._cache_by_id: Dict[str, Dict[str, Any]] = {}
        self._cache_by_phone: Dict[str, Dict[str, Any]] = {}
        self._cache_expiry: Dict[str, float] = {}
        self.ttl = 300  # 5 minutes cache

    def _is_expired(self, key: str) -> bool:
        expiry = self._cache_expiry.get(key)
        if expiry is None:
            return True
        return time.time() > expiry

    async def get_client_by_phone(self, whatsapp_number: str) -> Optional[dict]:
        """Fetch client by phone with memory caching."""
        if not self._is_expired(f"phone:{whatsapp_number}"):
            return self._cache_by_phone.get(whatsapp_number)

        try:
            client = await supabase_client.get_client()
            # Normalize for matching
            res = await client.table("clients").select("*").eq("whatsapp_number", whatsapp_number).eq("active", True).execute()
            
            if res.data:
                config = res.data[0]
                self._update_cache(config)
                return config
            return None
        except Exception as e:
            logger.error(f"[ClientManager] Error fetching client by phone: {e}")
            return None

    async def get_client_by_id(self, client_id: str) -> Optional[dict]:
        """Fetch client by ID with memory caching."""
        if not self._is_expired(f"id:{client_id}"):
            return self._cache_by_id.get(client_id)

        try:
            client = await supabase_client.get_client()
            res = await client.table("clients").select("*").eq("id", client_id).eq("active", True).execute()
            
            if res.data:
                config = res.data[0]
                self._update_cache(config)
                return config
            return None
        except Exception as e:
            logger.error(f"[ClientManager] Error fetching client by id: {e}")
            return None

    def _update_cache(self, config: dict):
        cid = config["id"]
        phone = config["whatsapp_number"]
        expiry = time.time() + self.ttl
        
        self._cache_by_id[cid] = config
        self._cache_by_phone[phone] = config
        self._cache_expiry[f"id:{cid}"] = expiry
        self._cache_expiry[f"phone:{phone}"] = expiry

    async def create_client(self, data: dict) -> dict:
        """Create new client and invalidate cache."""
        try:
            client = await supabase_client.get_client()
            res = await client.table("clients").insert(data).execute()
            if res.data:
                return res.data[0]
            return {}
        except Exception as e:
            logger.error(f"[ClientManager] Error creating client: {e}")
            return {}

    async def update_client(self, client_id: str, data: dict) -> dict:
        """Update client and invalidate cache."""
        try:
            client = await supabase_client.get_client()
            res = await client.table("clients").update(data).eq("id", client_id).execute()
            self.invalidate_cache(client_id)
            if res.data:
                return res.data[0]
            return {}
        except Exception as e:
            logger.error(f"[ClientManager] Error updating client: {e}")
            return {}

    async def list_clients(self) -> List[dict]:
        """List all active clients."""
        try:
            client = await supabase_client.get_client()
            res = await client.table("clients").select("id, business_name, whatsapp_number, active").execute()
            return res.data or []
        except Exception as e:
            logger.error(f"[ClientManager] Error listing clients: {e}")
            return []

    def invalidate_cache(self, client_id: Optional[str] = None):
        """Clear cache for specific client or all."""
        if client_id:
            config = self._cache_by_id.get(client_id)
            if config:
                phone = config.get("whatsapp_number")
                self._cache_by_id.pop(client_id, None)
                if phone:
                    self._cache_by_phone.pop(phone, None)
                    self._cache_expiry.pop(f"phone:{phone}", None)
                self._cache_expiry.pop(f"id:{client_id}", None)
        else:
            self._cache_by_id.clear()
            self._cache_by_phone.clear()
            self._cache_expiry.clear()

client_manager = ClientManager()
