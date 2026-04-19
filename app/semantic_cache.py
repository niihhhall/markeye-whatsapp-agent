import logging
from typing import Optional, Dict
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

# ⚠️ WARNING: ONLY cache global/brand info (pricing, FAQs). 
# NEVER cache results for user-specific questions (PII leak risk).

class SemanticCache:
    COMMON_INTENTS = {
        "pricing": ["price", "cost", "how much", "rates", "fee", 
                    "charge", "budget", "affordable", "expensive"],
        "how_it_works": ["how does", "how do you", "what do you do", 
                         "explain", "tell me about", "what is this",
                         "how it works"],
        "demo": ["demo", "show me", "example", "see it", "trial", 
                 "test", "try it"],
        "timeline": ["how long", "how fast", "when can", "setup time", 
                     "get started", "how quickly"],
        "who_are_you": ["who are you", "are you a bot", "are you ai",
                        "are you real", "human or bot"]
    }
    
    def __init__(self):
        # We'll use Redis for persistence across workers
        pass
    
    def detect_intent(self, message: str) -> Optional[str]:
        """Simple keyword-based intent detection."""
        message_lower = message.lower().strip()
        for intent, keywords in self.COMMON_INTENTS.items():
            if any(kw in message_lower for kw in keywords):
                return intent
        return None
    
    async def get_cached(self, client_id: str, message: str) -> Optional[str]:
        """Lookup cached response for intent."""
        intent = self.detect_intent(message)
        if not intent:
            return None
            
        # Cache is per client (tenant) to support unique pricing/demos
        cache_key = f"scache:{client_id or 'global'}:{intent}"
        cached = await redis_client.redis.get(cache_key)
        if cached:
            logger.info(f"[SemanticCache] ⚡ Hit! Intent: {intent}")
            await redis_client.inc_metric("semantic_cache_hits")
        return cached
    
    async def set_cache(self, client_id: str, message: str, response: str):
        """Store response for future hits."""
        intent = self.detect_intent(message)
        if intent:
            cache_key = f"scache:{client_id or 'global'}:{intent}"
            # Cache for 24 hours
            await redis_client.redis.set(cache_key, response, ex=86400)
            logger.info(f"[SemanticCache] 💾 Cached response for intent: {intent}")

semantic_cache = SemanticCache()
