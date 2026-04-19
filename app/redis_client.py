import json
import time
from redis.asyncio import from_url, Redis
from app.config import settings
from typing import Optional, List, Dict, Any

class RedisClient:
    def __init__(self):
        redis_url = settings.REDIS_URL
        kwargs = {"decode_responses": True}
        if redis_url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = "none"
        self.redis = from_url(redis_url, **kwargs)

    async def ping(self) -> bool:
        try:
            return await self.redis.ping()
        except Exception as e:
            print(f"[Redis] ERROR: Ping failed: {e}", flush=True)
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
            # Persistent memory: 7 days TTL
            await self.redis.set(f"session:{phone}", json.dumps(session), ex=604800)
            print(f"[Redis] OK: Session saved for {phone}", flush=True)
        except Exception as e:
            print(f"[Redis] ERROR: save_session failed for {phone}: {e}", flush=True)

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
            session["history"] = session["history"][-100:]
            await self.save_session(phone, session)
        except Exception as e:
            print(f"[Redis] ❌ add_to_history failed for {phone}: {e}", flush=True)

    async def check_dedup(self, message_sid: str) -> bool:
        """Returns True if seen, False if new. Stores with 5min TTL."""
        try:
            exists = await self.redis.get(f"dedup:{message_sid}")
            if exists:
                return True
            # Deduplicate for 24 hours to handle Meta's late retries
            await self.redis.set(f"dedup:{message_sid}", "1", ex=86400)
            return False
        except Exception as e:
            print(f"[Redis] ❌ check_dedup failed: {e}", flush=True)
            return False

    async def buffer_message(self, phone: str, message: str) -> tuple[str, bool]:
        """Adds message to input buffer list and tracks first message time. Returns (new_batch_id, is_first)."""
        import uuid
        try:
            key = f"buffer:{phone}"
            first_key = f"buffer_first:{phone}"
            batch_key = f"buffer_batch:{phone}"
            
            await self.redis.rpush(key, message)
            await self.redis.expire(key, 60)
            
            is_first = False
            if not await self.redis.exists(first_key):
                await self.redis.set(first_key, str(time.time()), ex=60)
                is_first = True
            
            new_batch_id = str(uuid.uuid4())
            await self.redis.set(batch_key, new_batch_id, ex=60)
                
            print(f"[Redis] ✅ Buffered message for {phone}, batch {new_batch_id}", flush=True)
            return new_batch_id, is_first
        except Exception as e:
            print(f"[Redis] ❌ buffer_message failed for {phone}: {e}", flush=True)
            return "error_batch", False

    async def get_and_clear_buffer(self, phone: str) -> str:
        """Returns all buffered messages joined correctly and clears the buffer."""
        try:
            key = f"buffer:{phone}"
            first_key = f"buffer_first:{phone}"
            batch_key = f"buffer_batch:{phone}"
            
            messages = await self.redis.lrange(key, 0, -1)
            await self.redis.delete(key, first_key, batch_key)
            
            if not messages:
                return ""
            
            # Using decode_responses=True in from_url, so messages are already strings
            return "\n".join(messages)
        except Exception as e:
            print(f"[Redis] ❌ get_and_clear_buffer failed for {phone}: {e}", flush=True)
            return ""

    async def is_batch_current(self, phone: str, batch_id: str) -> bool:
        """Check if this batch_id is still current (no new messages since)."""
        try:
            current = await self.redis.get(f"buffer_batch:{phone}")
            if not current:
                return False
            return current == batch_id
        except Exception as e:
            print(f"[Redis] ❌ is_batch_current failed: {e}", flush=True)
            return False

    async def has_hit_hard_max(self, phone: str) -> bool:
        """Check if 8 seconds passed since first message in batch."""
        try:
            first_ts = await self.redis.get(f"buffer_first:{phone}")
            if not first_ts:
                return False
            return (time.time() - float(first_ts)) >= settings.INPUT_BUFFER_MAX_SECONDS
        except Exception as e:
            print(f"[Redis] ❌ has_hit_hard_max failed: {e}", flush=True)
            return False

    async def set_generating(self, phone: str):
        try:
            await self.redis.set(f"generating:{phone}", "1", ex=120)
            await self.redis.set(f"generating_ts:{phone}", str(time.time()), ex=120)
        except Exception as e:
            print(f"[Redis] ❌ set_generating failed: {e}", flush=True)

    async def clear_generating(self, phone: str):
        try:
            await self.redis.delete(f"generating:{phone}", f"generating_ts:{phone}")
        except Exception as e:
            print(f"[Redis] ❌ clear_generating failed: {e}", flush=True)

    async def is_generating(self, phone: str) -> bool:
        try:
            exists = await self.redis.exists(f"generating:{phone}")
            return exists > 0
        except Exception as e:
            print(f"[Redis] ❌ is_generating failed: {e}", flush=True)
            return False

    async def has_new_messages(self, phone: str) -> bool:
        """Check if new messages arrived in buffer (during generation)."""
        try:
            length = await self.redis.llen(f"buffer:{phone}")
            return length > 0
        except Exception as e:
            print(f"[Redis] ❌ has_new_messages failed: {e}", flush=True)
            return False

    async def lrange(self, key: str, start: int, stop: int) -> List[str]:
        """Expose lrange from underlying redis."""
        try:
            return await self.redis.lrange(key, start, stop)
        except Exception as e:
            print(f"[Redis] ❌ lrange failed for {key}: {e}", flush=True)
            return []

    async def has_sent_calendly(self, phone: str) -> bool:
        exists = await self.redis.exists(f"calendly_sent:{phone}")
        return exists > 0

    async def mark_calendly_sent(self, phone: str):
        await self.redis.set(f"calendly_sent:{phone}", "1", ex=86400 * 7)

    async def check_and_clear_stale_generation(self, phone: str):
        try:
            ts_key = f"generating_ts:{phone}"
            gen_key = f"generating:{phone}"
            
            ts = await self.redis.get(ts_key)
            if ts and (time.time() - float(ts)) > 120:
                print(f"[Redis] WARN: Stale generation flag for {phone}, clearing", flush=True)
                await self.redis.delete(gen_key, ts_key)
        except Exception as e:
            print(f"[Redis] ERROR: check_and_clear_stale_generation failed: {e}", flush=True)

    async def get(self, key: str) -> Optional[str]:
        """Generic get for RAG context."""
        try:
            return await self.redis.get(key)
        except Exception as e:
            print(f"[Redis] ❌ get failed for {key}: {e}", flush=True)
            return None

    async def set(self, key: str, value: str, ex: int = None):
        """Generic set for RAG context initialization."""
        try:
            await self.redis.set(key, value, ex=ex)
        except Exception as e:
            print(f"[Redis] ❌ set failed for {key}: {e}", flush=True)

    # Telemetry Helpers
    async def inc_metric(self, name: str, amount: int = 1):
        """Atomic counter increment."""
        try:
            await self.redis.incrby(f"metrics:{name}", amount)
        except:
            pass

    async def get_metrics(self) -> Dict[str, int]:
        """Fetch all current global metrics."""
        try:
            keys = await self.redis.keys("metrics:*")
            results = {}
            for k in keys:
                val = await self.redis.get(k)
                results[k.replace("metrics:", "")] = int(val) if val else 0
            return results
        except:
            return {}
            
    async def log_llm_metric(self, provider: str, tokens: int = 0):
        """Specialized LLM usage tracker."""
        await self.inc_metric("total_llm_calls")
        await self.inc_metric(f"llm_provider:{provider.lower()}")
        if tokens:
            await self.inc_metric("total_tokens", tokens)

redis_client = RedisClient()
