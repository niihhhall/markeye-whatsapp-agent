import logging
import asyncio
import random
import httpx
from app.config import settings
from app.chunker import calculate_typing_delay

logger = logging.getLogger(__name__)

BASE_URL = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _to_wa_phone(phone: str) -> str:
    """'whatsapp:+447700900000' → '447700900000'"""
    return phone.replace("whatsapp:", "").replace("+", "")


async def send_message(to: str, body: str) -> dict | None:
    """Send a text message via WhatsApp Cloud API."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _to_wa_phone(to),
        "type": "text",
        "text": {"body": body}
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{BASE_URL}/messages", headers=_headers(), json=payload)
            if resp.status_code == 200:
                logger.info(f"Sent to {to}: {body[:50]}...")
                return resp.json()
            else:
                logger.error(f"Send failed: {resp.status_code} — {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Send error: {e}")
        return None


async def send_chunked_messages(to: str, chunks: list[str], conversation_id: str = "", message_id: str = "") -> None:
    """Send multiple messages with realistic typing delays and interrupt check."""
    from app.redis_client import redis_client
        # 1. Start Typing Indicator
        await send_typing_indicator(to)

        # 2. Thinking/Typing Delay
        if i == 0:
            # First bubble delay (thinking)
            delay = random.uniform(2.0, 4.0)
        else:
            # Subsequent bubble delay (typing)
            delay = calculate_typing_delay(chunk)
            
        # Simulate typing/thinking period - refresh typing every 10s if long
        intervals = int(delay / 1.0) # Check every second
        for sec in range(intervals):
            await asyncio.sleep(1.0)
            # Refresh typing indicator if we've waited more than 10s (Meta expires it at 25s)
            if sec > 0 and sec % 10 == 0:
                await send_typing_indicator(to)
            
            if await redis_client.has_new_messages(to):
                logger.info(f"Interrupt: New message during delay for {to}. Aborting.")
                return
        await asyncio.sleep(delay % 1.0)
            
        await send_message(to, chunk)


async def send_template_message(to: str, template_name: str, language_code: str = "en_US", components: list = None) -> dict | None:
    """Send a WhatsApp template message."""
    payload = {
        "messaging_product": "whatsapp",
        "to": _to_wa_phone(to),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code}
        }
    }
    if components:
        payload["template"]["components"] = components

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{BASE_URL}/messages", headers=_headers(), json=payload)
            if resp.status_code == 200:
                logger.info(f"Template {template_name} sent to {to}")
                return resp.json()
            else:
                logger.error(f"Template failed: {resp.status_code} — {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Template error: {e}")
        return None


async def send_typing_indicator(to: str, message_id: str = "") -> bool:
    """Send a typing indicator using sender_action."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _to_wa_phone(to),
        "sender_action": "typing_on"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{BASE_URL}/messages", headers=_headers(), json=payload)
            if resp.status_code == 200:
                logger.info(f"Typing indicator sent to {to}")
                return True
            else:
                # Some accounts might not support this yet, log and continue
                logger.info(f"Typing indicator not supported/failed: {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Typing indicator error: {e}")
        return False


async def mark_as_read(message_id: str) -> None:
    """Mark a message as read (blue ticks)."""
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{BASE_URL}/messages", headers=_headers(), json=payload)
            if resp.status_code == 200:
                logger.info(f"Marked as read: {message_id}")
    except Exception as e:
        logger.error(f"Mark as read error: {e}")
