import json
import logging
import asyncio
import httpx
from app.config import settings
from app.chunker import calculate_typing_delay

logger = logging.getLogger(__name__)

# WhatsApp Cloud API (Meta) Graph URL
BASE_URL = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"

def _get_headers() -> dict:
    """Return auth headers for WhatsApp Cloud API."""
    return {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

def _to_cloud_phone(phone: str) -> str:
    """
    Convert internal format to Cloud API format.
    Our system:  whatsapp:+447700900000
    Cloud API:    447700900000
    """
    return phone.replace("whatsapp:", "").replace("+", "")

def _to_internal_phone(phone: str) -> str:
    """
    Convert Cloud API phone format to internal format.
    Cloud API:    447700900000
    Our system:  whatsapp:+447700900000
    """
    cleaned = str(phone).strip()
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return "whatsapp:" + cleaned

async def send_message(to: str, body: str) -> dict | None:
    """
    Send a WhatsApp message via Cloud API.
    """
    cloud_phone = _to_cloud_phone(to)
    
    url = f"{BASE_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": cloud_phone,
        "type": "text",
        "text": {"body": body},
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            print(f"WhatsApp Cloud: sending to {cloud_phone}", flush=True)
            logger.info("WhatsApp Cloud: sending to %s", cloud_phone)
            response = await client.post(
                url,
                headers=_get_headers(),
                json=payload,
            )
            if response.status_code in (200, 201, 202):
                logger.info("WhatsApp Cloud: message sent to %s", cloud_phone)
                return response.json()
            else:
                logger.error(
                    "WhatsApp Cloud send failed: %s \u2014 %s",
                    response.status_code,
                    response.text
                )
                return None
    except Exception as exc:
        logger.error("WhatsApp Cloud send error: %s", exc)
        return None

async def mark_as_read(message_id: str) -> bool:
    """
    Mark a WhatsApp message as read via Cloud API.
    """
    if not message_id:
        return False
        
    url = f"{BASE_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers=_get_headers(),
                json=payload,
            )
            if response.status_code in (200, 204, 202):
                logger.info("WhatsApp Cloud: message %s marked as read", message_id)
                return True
            logger.warning("WhatsApp Cloud mark_as_read failed: %s \u2014 %s", response.status_code, response.text)
            return False
    except Exception as exc:
        logger.error("WhatsApp Cloud mark_as_read error: %s", exc)
        return False

async def send_typing_indicator(to: str, message_id: str = "") -> bool:
    """
    Send a typing indicator to the WhatsApp user.
    Requires message_id to link the indicator to the conversation.
    """
    if not message_id:
        return False
        
    cloud_phone = _to_cloud_phone(to)
    url = f"{BASE_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": cloud_phone,
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"}
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            print(f"WhatsApp Cloud: typing indicator to {cloud_phone} for msg {message_id}", flush=True)
            response = await client.post(
                url,
                headers=_get_headers(),
                json=payload,
            )
            if response.status_code not in (200, 204, 202):
                logger.error("WhatsApp Cloud typing indicator failed: %s - %s", response.status_code, response.text)
                print(f"[Typing] ❌ Failed: {response.text}", flush=True)
            return response.status_code in (200, 204, 202)
    except Exception as exc:
        logger.error("WhatsApp Cloud typing indicator error: %s", exc)
        return False

async def send_chunked_messages(to: str, chunks: list[str], conversation_id: str = "", message_id: str = "") -> None:
    """
    Send multiple messages with realistic typing delays.
    Shows typing indicator during the sleep period for better UX.
    """
    for i, chunk in enumerate(chunks):
        if i > 0:
            delay = calculate_typing_delay(chunk)
            if message_id:
                # Fire and forget typing indicator
                asyncio.create_task(send_typing_indicator(to, message_id))
            await asyncio.sleep(delay)
        
        await send_message(to, chunk)

async def get_media_url(media_id: str) -> str | None:
    """
    Get the temporary download URL for a media file via its ID.
    """
    url = f"{BASE_URL}/{media_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=_get_headers())
            if response.status_code == 200:
                data = response.json()
                return data.get("url")
            else:
                logger.error("WhatsApp Cloud get_media_url failed: %s - %s", response.status_code, response.text)
                return None
    except Exception as exc:
        logger.error("WhatsApp Cloud get_media_url error: %s", exc)
        return None

async def download_media(url: str) -> bytes | None:
    """
    Download a media file using its temporary URL.
    This requires the same Bearer token.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=_get_headers())
            if response.status_code == 200:
                return response.content
            else:
                logger.error("WhatsApp Cloud download_media failed: %s - %s", response.status_code, response.text)
                return None
    except Exception as exc:
        logger.error("WhatsApp Cloud download_media error: %s", exc)
        return None
