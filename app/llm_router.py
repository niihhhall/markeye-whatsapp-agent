import time
import logging
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI, RateLimitError, APIStatusError
from app.config import settings

logger = logging.getLogger(__name__)

class SmartLLMRouter:
    def __init__(self):
        """
        Initializes OpenAI-compatible clients for Groq, Gemini, and Cerebras.
        Fallback order: Groq -> Gemini -> Cerebras -> (Optional) OpenRouter.
        """
        self.providers = []
        
        # 1. Groq (Primary)
        if settings.GROQ_API_KEY:
            self.providers.append({
                "name": "Groq",
                "client": AsyncOpenAI(api_key=settings.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1"),
                "model": settings.GROQ_MODEL
            })
            
        # 2. Gemini (Secondary)
        if settings.GEMINI_API_KEY:
            self.providers.append({
                "name": "Gemini",
                "client": AsyncOpenAI(api_key=settings.GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/"),
                "model": settings.GEMINI_MODEL
            })
            
        # 3. Cerebras (Fallback)
        if settings.CEREBRAS_API_KEY:
            self.providers.append({
                "name": "Cerebras",
                "client": AsyncOpenAI(api_key=settings.CEREBRAS_API_KEY, base_url="https://api.cerebras.ai/v1"),
                "model": settings.CEREBRAS_MODEL
            })
            
        # 4. OpenRouter (Legacy Fallback)
        if settings.OPENROUTER_API_KEY:
            self.providers.append({
                "name": "OpenRouter",
                "client": AsyncOpenAI(api_key=settings.OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1"),
                "model": "openai/gpt-4o-mini"
            })

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
            model = model_override or provider["model"]
            
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
