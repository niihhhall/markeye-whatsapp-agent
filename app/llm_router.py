import time
import logging
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI, RateLimitError, APIStatusError
from app.config import settings

logger = logging.getLogger(__name__)

class SmartLLMRouter:
    def __init__(self):
        """
        Fireworks AI is the ONLY provider. Three models on one key form the
        fallback chain: primary -> secondary -> fallback.
        (glm-5p2 -> deepseek-v4-pro -> kimi-k2p6)
        """
        self.providers = []

        # ── Fireworks AI (only provider) ──────────────────────────────────
        if settings.FIREWORKS_API_KEY:
            fw_client = AsyncOpenAI(
                api_key=settings.FIREWORKS_API_KEY,
                base_url=settings.FIREWORKS_BASE_URL,
            )
            self.providers.append({
                "name": "Fireworks-Primary",
                "client": fw_client,
                "model": settings.FIREWORKS_PRIMARY_MODEL,
            })
            self.providers.append({
                "name": "Fireworks-Secondary",
                "client": fw_client,
                "model": settings.FIREWORKS_SECONDARY_MODEL,
            })
            self.providers.append({
                "name": "Fireworks-Fallback",
                "client": fw_client,
                "model": settings.FIREWORKS_FALLBACK_MODEL,
            })
        else:
            logger.error("[SmartLLMRouter] FIREWORKS_API_KEY not set — no LLM providers configured.")

    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model_override: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Attempts to get a completion from providers in order.
        Returns a dict with 'content', 'model', 'provider', 'usage', 'id', and 'latency_ms'.
        """
        if not self.providers:
            # Fallback to standard OpenAI if no router providers are configured
            logger.warning("[SmartLLMRouter] No router providers configured. Using default OpenAI client.")
            default_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            response = await default_client.chat.completions.create(
                model=model_override or settings.PRIMARY_MODEL,
                messages=messages,
                response_format=response_format,
                **kwargs
            )
            return {
                "content": response.choices[0].message.content,
                "model": model_override or settings.PRIMARY_MODEL,
                "provider": "OpenAI",
                "usage": response.usage,
                "id": response.id,
                "latency_ms": 0
            }

        last_error = None
        for provider in self.providers:
            provider_name = provider["name"]
            client = provider["client"]
            # A model name is provider-specific. Never force one provider's model
            # (e.g. a Gemini model) onto another provider — that caused every
            # fallback to 404. Always use each provider's own configured model.
            # model_override is only honored when it matches this provider's model.
            model = provider["model"]
            if model_override and model_override == provider["model"]:
                model = model_override
            
            try:
                start_time = time.time()
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    timeout=timeout,
                    **kwargs
                )
                latency_ms = int((time.time() - start_time) * 1000)
                
                logger.info(f"[SmartLLMRouter] ✅ {provider_name} success | Model: {model} | Latency: {latency_ms}ms")
                
                return {
                    "content": response.choices[0].message.content,
                    "model": model,
                    "provider": provider_name,
                    "usage": response.usage,
                    "id": response.id,
                    "latency_ms": latency_ms
                }
                
            except (RateLimitError, APIStatusError) as e:
                logger.warning(f"[SmartLLMRouter] ⚠️ {provider_name} failed (RateLimit/API): {e}")
                # Track fallback events in Redis for /metrics endpoint
                try:
                    from app.redis_client import redis_client
                    await redis_client.redis.incr(f"metrics:llm_fallback:{provider_name.lower()}")
                except Exception:
                    pass
                last_error = e
                continue
            except Exception as e:
                logger.error(f"[SmartLLMRouter] ❌ {provider_name} critical error: {e}")
                last_error = e
                continue
                
        logger.error("[SmartLLMRouter] 💀 All LLM providers exhausted.")
        import sentry_sdk
        sentry_sdk.capture_exception(last_error or Exception("All LLM providers exhausted"))
        raise last_error or Exception("No LLM providers available and functional.")

llm_router = SmartLLMRouter()
