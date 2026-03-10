import json
import redis.asyncio as redis
from app.config import settings
from typing import Optional, List, Dict, Any

class RedisClient:
    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def ping(self) -> bool:
        try:
            return await self.redis.ping()
        except Exception as e:
            print(f"[Redis] ❌ Ping failed: {e}", flush=True)
            return False

    async def get_session(self, phone: str) -> Optional[Dict[str, Any]]:
        try:
            data = await self.redis.get(f"session:{phone}")
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            print(f"[Redis] ❌ get_session failed for {phone}: {e}", flush=True)
            return None

    async def save_session(self, phone: str, session: Dict[str, Any]):
        try:
            await self.redis.set(f"session:{phone}", json.dumps(session), ex=86400)
            print(f"[Redis] ✅ Session saved for {phone}", flush=True)
        except Exception as e:
            print(f"[Redis] ❌ save_session failed for {phone}: {e}", flush=True)

    async def add_to_history(self, phone: str, role: str, content: str):
        try:
            session = await self.get_session(phone)
            if not session:
                session = {
                    "state": "opening",
                    "history": [],
                    "turn_count": 0,
                    "lead_data": {}
                }
            
            session["history"].append({"role": role, "content": content})
            session["history"] = session["history"][-10:]
            await self.save_session(phone, session)
        except Exception as e:
            print(f"[Redis] ❌ add_to_history failed for {phone}: {e}", flush=True)

    async def check_dedup(self, message_sid: str) -> bool:
        """Returns True if seen, False if new. Stores with 5min TTL."""
        try:
            exists = await self.redis.get(f"dedup:{message_sid}")
            if exists:
                return True
            await self.redis.set(f"dedup:{message_sid}", "1", ex=300)
            return False
        except Exception as e:
            print(f"[Redis] ❌ check_dedup failed: {e}", flush=True)
            return False

    async def buffer_message(self, phone: str, message: str):
        """Adds message to input buffer list."""
        try:
            await self.redis.rpush(f"buffer:{phone}", message)
            await self.redis.expire(f"buffer:{phone}", 60)
            print(f"[Redis] ✅ Buffered message for {phone}", flush=True)
        except Exception as e:
            print(f"[Redis] ❌ buffer_message failed for {phone}: {e}", flush=True)

    async def get_and_clear_buffer(self, phone: str) -> List[str]:
        """Returns all buffered messages and clears the buffer."""
        try:
            messages = await self.redis.lrange(f"buffer:{phone}", 0, -1)
            await self.redis.delete(f"buffer:{phone}")
            return messages
        except Exception as e:
            print(f"[Redis] ❌ get_and_clear_buffer failed for {phone}: {e}", flush=True)
            return []

    async def set_buffer_timer(self, phone: str):
        """Sets a key with 3-second TTL to track buffer window."""
        try:
            await self.redis.set(f"timer:{phone}", "1", ex=settings.INPUT_BUFFER_SECONDS)
            print(f"[Redis] ✅ Timer set for {phone}", flush=True)
        except Exception as e:
            print(f"[Redis] ❌ set_buffer_timer failed for {phone}: {e}", flush=True)

    async def is_timer_active(self, phone: str) -> bool:
        try:
            return await self.redis.exists(f"timer:{phone}") > 0
        except Exception as e:
            print(f"[Redis] ❌ is_timer_active failed for {phone}: {e}", flush=True)
            return False

redis_client = RedisClient()
