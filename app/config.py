from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    # Twilio
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_WHATSAPP_NUMBER: str

    # OpenRouter
    OPENROUTER_API_KEY: str
    OPENROUTER_PRIMARY_MODEL: str = "anthropic/claude-3.5-sonnet"
    OPENROUTER_FALLBACK_MODEL: str = "google/gemini-flash-1.5"
    OPENROUTER_BANT_MODEL: str = "google/gemini-flash-1.5"

    # Redis
    REDIS_URL: str

    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str

    # Helicone (optional)
    HELICONE_API_KEY: str | None = None

    # Calendly
    CALENDLY_LINK: str

    # App
    DEBUG: bool = False
    INPUT_BUFFER_SECONDS: int = 3
    TYPING_DELAY_PER_CHAR: float = 0.03
    CHUNK_DELAY_SECONDS: float = 1.5
    MAX_FOLLOWUPS: int = 3

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings():
    return Settings()

settings = get_settings()
