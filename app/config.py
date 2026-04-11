from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os

class Settings(BaseSettings):
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    # WhatsApp Cloud API (Meta)
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "markeye_verify_token")
    WHATSAPP_API_VERSION: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")
    MESSAGING_PROVIDER: str = "whatsapp_cloud"

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    PRIMARY_MODEL: str = "gpt-4o"
    FALLBACK_MODEL: str = "gpt-4o-mini"
    BANT_MODEL: str = "gpt-4o-mini"

    # Groq (Primary)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Gemini (Secondary)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    # Cerebras (Fallback)
    CEREBRAS_API_KEY: str = os.getenv("CEREBRAS_API_KEY", "")
    CEREBRAS_MODEL: str = os.getenv("CEREBRAS_MODEL", "llama3.1-70b")

    # Legacy OpenRouter (Optional Fallback)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

    # Redis
    REDIS_URL: str
    UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
    UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_SERVICE_KEY: str

    # Sentry
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")

    # Calendly
    CALENDLY_LINK: str = "https://calendly.com/markeye/free-discovery-call"

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
    SALES_PHONE_NUMBER: str = os.getenv("SALES_PHONE_NUMBER", "")
    PRICING_PDF_URL: str = os.getenv("PRICING_PDF_URL", "https://markeye.io/pricing-overview.pdf")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings():
    s = Settings()
    print(f"[Config] Active Messaging Provider: {s.MESSAGING_PROVIDER}", flush=True)
    return s

settings = get_settings()
