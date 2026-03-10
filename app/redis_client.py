import json
import time
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
                from app.models import ConversationState
                session = {
                    "state": ConversationState.OPENING,
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
        """Adds message to input buffer list and tracks first message time."""
        try:
            key = f"buffer:{phone}"
            first_key = f"buffer_first:{phone}"
            
            await self.redis.rpush(key, message)
            await self.redis.expire(key, 60)
            
            if not await self.redis.exists(first_key):
                await self.redis.set(first_key, str(time.time()), ex=60)
                
            print(f"[Redis] ✅ Buffered message for {phone}", flush=True)
        except Exception as e:
            print(f"[Redis] ❌ buffer_message failed for {phone}: {e}", flush=True)

    async def get_and_clear_buffer(self, phone: str) -> List[str]:
        """Returns all buffered messages and clears the buffer."""
        try:
            key = f"buffer:{phone}"
            first_key = f"buffer_first:{phone}"
            
            messages = await self.redis.lrange(key, 0, -1)
            await self.redis.delete(key)
            await self.redis.delete(first_key)
            return messages
        except Exception as e:
            print(f"[Redis] ❌ get_and_clear_buffer failed for {phone}: {e}", flush=True)
            return []

    async def should_process_buffer(self, phone: str) -> bool:
        """
        Check if we should process the buffer now.
        Returns True if 8 seconds have passed since the first message (hard max).
        """
        try:
            first_key = f"buffer_first:{phone}"
            first_ts = await self.redis.get(first_key)
            
            if not first_ts:
                return False
            
            first_time = float(first_ts)
            now = time.time()
            
            if now - first_time >= settings.INPUT_BUFFER_MAX_SECONDS:
                return True
            
            return False
        except Exception as e:
            print(f"[Redis] ❌ should_process_buffer failed for {phone}: {e}", flush=True)
            return False

    async def set_batch_id(self, phone: str, batch_id: str):
        await self.redis.set(f"batch:{phone}", batch_id, ex=60)

    async def get_batch_id(self, phone: str) -> Optional[str]:
        return await self.redis.get(f"batch:{phone}")

    async def is_processing(self, phone: str) -> bool:
        return await self.redis.exists(f"processing:{phone}") > 0

    async def set_processing(self, phone: str, active: bool = True):
        if active:
            await self.redis.set(f"processing:{phone}", "1", ex=60)
        else:
            await self.redis.delete(f"processing:{phone}")

    async def lrange(self, key: str, start: int, stop: int) -> List[str]:
        """Expose lrange from underlying redis."""
        try:
            return await self.redis.lrange(key, start, stop)
        except Exception as e:
            print(f"[Redis] ❌ lrange failed for {key}: {e}", flush=True)
            return []

    async def has_sent_calendly(self, phone: str) -> bool:
        return await self.redis.exists(f"calendly_sent:{phone}") > 0

    async def mark_calendly_sent(self, phone: str):
        await self.redis.set(f"calendly_sent:{phone}", "1", ex=86400 * 7)

redis_client = RedisClient()
