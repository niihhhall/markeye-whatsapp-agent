from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os

class Settings(BaseSettings):
    ENVIRONMENT: str = "production"
    # WhatsApp Cloud API (Meta)
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = "markeye_verify_token"
    WHATSAPP_APP_SECRET: str = ""  # Meta App Secret for HMAC webhook verification
    WHATSAPP_API_VERSION: str = "v21.0"
    MESSAGING_PROVIDER: str = "whatsapp_cloud"

    # OpenAI (Fix 9: Only used for Knowledge Base Embeddings)
    OPENAI_API_KEY: str = ""

    # Groq (Primary)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Gemini (Secondary)
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"

    # Cerebras (Fallback)
    CEREBRAS_API_KEY: str = ""
    CEREBRAS_MODEL: str = "llama3.1-70b"

    # Legacy OpenRouter (Optional Fallback)
    OPENROUTER_API_KEY: str = ""

    # Redis
    REDIS_URL: str
    UPSTASH_REDIS_REST_URL: str = ""
    UPSTASH_REDIS_REST_TOKEN: str = ""

    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_SERVICE_KEY: str

    # Sentry
    SENTRY_DSN: str = ""

    # Booking Link (Fix 12: Resolving Cal.com vs legacy naming)
    CALCOM_LINK: str = "https://cal.com/markeye/free-discovery-call"
    CALENDLY_LINK: str = ""  # Deprecated alias — kept for backward compat only

    @property
    def booking_link(self) -> str:
        """Returns the active booking link. Prefers Cal.com over legacy Calendly."""
        return self.CALCOM_LINK or self.CALENDLY_LINK

    # Security (Fix 1)
    ALLOWED_ORIGINS: str = "http://localhost:3000"  # comma-separated list in .env

    # App
    DEBUG: bool = False
    # Input buffer settings (V4: 5 second silence window)
    INPUT_BUFFER_SECONDS: float = 5.0
    INPUT_BUFFER_MAX_SECONDS: float = 8.0
    MAX_INTERRUPT_RETRIES: int = 2

    # Low content spam threshold
    LOW_CONTENT_THRESHOLD: int = 3
    TYPING_DELAY_PER_CHAR: float = 0.1
    CHUNK_DELAY_SECONDS: float = 1.5
    MAX_FOLLOWUPS: int = 3
    MAX_CHUNKS: int = 3

    # Voice / Whisper
    VOICE_NOTE_ACKNOWLEDGE: bool = True
    VOICE_NOTE_ACK_MESSAGE: str = ""

    # Human-like Behavior
    MARK_AS_READ_DELAY: float = 2.0
    SHOW_TYPING_INDICATOR: bool = True

    # Baileys Settings
    BAILEYS_AUTH_DIR: str = "./baileys-service/sessions"
    WHATSAPP_INBOUND_CHANNEL: str = "inbound"
    WHATSAPP_OUTBOUND_CHANNEL: str = "outbound"
    SALES_PHONE_NUMBER: str = ""
    PRICING_PDF_URL: str = "https://markeye.io/pricing-overview.pdf"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings():
    import logging
    logger = logging.getLogger(__name__)
    s = Settings()
    logger.info(f"[Config] Active Messaging Provider: {s.MESSAGING_PROVIDER}")
    return s

settings = get_settings()
