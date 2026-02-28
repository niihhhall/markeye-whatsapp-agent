import json
import redis.asyncio as redis
from app.config import settings
from typing import Optional, List, Dict, Any

class RedisClient:
    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def get_session(self, phone: str) -> Optional[Dict[str, Any]]:
        data = await self.redis.get(f"session:{phone}")
        if data:
            return json.loads(data)
        return None

    async def save_session(self, phone: str, session: Dict[str, Any]):
        await self.redis.set(f"session:{phone}", json.dumps(session), ex=86400)  # 24h TTL

    async def add_to_history(self, phone: str, role: str, content: str):
        session = await self.get_session(phone)
        if not session:
            session = {
                "state": "opening",
                "history": [],
                "turn_count": 0,
                "lead_data": {}
            }
        
        session["history"].append({"role": role, "content": content})
        # Keep last 10 messages
        session["history"] = session["history"][-10:]
        await self.save_session(phone, session)

    async def check_dedup(self, message_sid: str) -> bool:
        """Returns True if seen, False if new. Stores with 5min TTL."""
        exists = await self.redis.get(f"dedup:{message_sid}")
        if exists:
            return True
        await self.redis.set(f"dedup:{message_sid}", "1", ex=300)
        return False

    async def buffer_message(self, phone: str, message: str):
        """Adds message to input buffer list."""
        await self.redis.rpush(f"buffer:{phone}", message)
        await self.redis.expire(f"buffer:{phone}", 60) # Fail-safe TTL

    async def get_and_clear_buffer(self, phone: str) -> List[str]:
        """Returns all buffered messages and clears the buffer."""
        messages = await self.redis.lrange(f"buffer:{phone}", 0, -1)
        await self.redis.delete(f"buffer:{phone}")
        return messages

    async def set_buffer_timer(self, phone: str):
        """Sets a key with 3-second TTL to track buffer window."""
        await self.redis.set(f"timer:{phone}", "1", ex=settings.INPUT_BUFFER_SECONDS)

    async def is_timer_active(self, phone: str) -> bool:
        return await self.redis.exists(f"timer:{phone}") > 0

redis_client = RedisClient()
