import logging
import io
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"


async def process_voice_note_from_media_id(media_id: str) -> str | None:
    """
    Full pipeline for WhatsApp Cloud API voice notes:
    1. Get media URL from media_id
    2. Download audio bytes
    3. Transcribe with Whisper
    """
    # Step 1: Get media URL
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{media_id}",
                headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
            )
            if resp.status_code != 200:
                logger.error(f"Media URL fetch failed: {resp.status_code} - {resp.text}")
                return None
            media_url = resp.json().get("url")
    except Exception as e:
        logger.error(f"Media URL error: {e}")
        return None
    
    if not media_url:
        logger.error("No media URL returned from Meta API")
        return None
    
    # Step 2: Download audio (URL expires in ~5 minutes!)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
            )
            if resp.status_code != 200:
                logger.error(f"Audio download failed: {resp.status_code} - {resp.text}")
                return None
            audio_bytes = resp.content
            logger.info(f"Audio downloaded: {len(audio_bytes)} bytes")
    except Exception as e:
        logger.error(f"Audio download error: {e}")
        return None
    
    # Step 3: Transcribe with Whisper
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                WHISPER_URL,
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                files={"file": ("voice.ogg", io.BytesIO(audio_bytes), "audio/ogg")},
                data={"model": "whisper-1", "language": "en"},
            )
            if resp.status_code == 200:
                text = resp.json().get("text", "").strip()
                logger.info(f"Transcription: {text[:80]}...")
                return text if text else None
            else:
                logger.error(f"Whisper error: {resp.status_code} - {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return None
