from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os

class Settings(BaseSettings):
    # MessageBird / Bird
    MESSAGEBIRD_API_KEY: str = os.getenv("MESSAGEBIRD_API_KEY", "")
    MESSAGEBIRD_WORKSPACE_ID: str = os.getenv("MESSAGEBIRD_WORKSPACE_ID", "")
    MESSAGEBIRD_CHANNEL_ID: str = os.getenv("MESSAGEBIRD_CHANNEL_ID", "")
    MESSAGEBIRD_WHATSAPP_NUMBER: str = ""  # for reference only


    # WhatsApp Cloud API (Meta)
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "after5_verify_token")
    WHATSAPP_API_VERSION: str = os.getenv("WHATSAPP_API_VERSION", "v22.0")
    MESSAGING_PROVIDER: str = os.getenv("MESSAGING_PROVIDER", "messagebird") # "messagebird" or "whatsapp_cloud"

    # OpenRouter
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_PRIMARY_MODEL: str = "anthropic/claude-3.5-sonnet"
    OPENROUTER_FALLBACK_MODEL: str = "openai/gpt-4o-mini"
    OPENROUTER_BANT_MODEL: str = "openai/gpt-4o-mini"

    # Redis
    REDIS_URL: str

    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_SERVICE_KEY: str

    # Helicone (optional)
    HELICONE_API_KEY: str | None = None

    # Calendly
    CALENDLY_LINK: str = "https://calendly.com/after5/free-discovery-call"

    # App
    DEBUG: bool = False
    # Input buffer settings (Issues 1 & 2)
    INPUT_BUFFER_SECONDS: float = 3.0       # Rolling timer
    INPUT_BUFFER_MAX_SECONDS: float = 8.0   # Hard max from first message
    
    # Low content spam threshold (Issue 8)
    LOW_CONTENT_THRESHOLD: int = 3          # Messages before WAITING state
    TYPING_DELAY_PER_CHAR: float = 0.03
    CHUNK_DELAY_SECONDS: float = 1.5
    MAX_FOLLOWUPS: int = 2
    MAX_CHUNKS: int = 3

    # OpenAI / Whisper
    OPENAI_API_KEY: str = ""
    VOICE_NOTE_ACKNOWLEDGE: bool = True
    VOICE_NOTE_ACK_MESSAGE: str = "" # "Got your voice note, let me listen..."

    # Human-like Behavior
    MARK_AS_READ_DELAY: float = 2.0
    SHOW_TYPING_INDICATOR: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings():
    s = Settings()
    print(f"[Config] 🛠️ Active Messaging Provider: {s.MESSAGING_PROVIDER}", flush=True)
    return s

settings = get_settings()
