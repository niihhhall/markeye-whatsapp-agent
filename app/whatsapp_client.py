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


async def send_chunked_messages(
    to: str, 
    chunks: list[str], 
    incoming_text: str = "", 
    last_message_ts: float = 0, 
    message_id: str = ""
) -> None:
    """Send multiple messages with realistic human-like timing sequence."""
    from app.chunker import calculate_chunk_sequence, format_message
    from app.redis_client import redis_client
    import time

    current_time = time.time()
    sequences = calculate_chunk_sequence(incoming_text, chunks, last_message_ts, current_time)

    for i, chunk in enumerate(chunks):
        seq = sequences[i]
        
        # 1. Blue Tick Delay
        if seq["blue_tick_delay"] > 0:
            await asyncio.sleep(seq["blue_tick_delay"])
            if message_id:
                await mark_as_read(message_id)

        # 2. Reading Delay
        if seq["reading_delay"] > 0:
            await asyncio.sleep(seq["reading_delay"])

        # 3. Think Pause
        if seq["think_pause"] > 0:
            await asyncio.sleep(seq["think_pause"])

        # 4. Typing Delay (with Indicator)
        if seq["typing_delay"] > 0:
            await send_typing_indicator(to, message_id)
            
            # Check for interrupts during typing
            intervals = int(seq["typing_delay"] / 1.0)
            for _ in range(intervals):
                await asyncio.sleep(1.0)
                if await redis_client.has_new_messages(to):
                    logger.info(f"Interrupt: New message during typing for {to}. Aborting.")
                    return
            await asyncio.sleep(seq["typing_delay"] % 1.0)

        # 5. Review Pause
        if seq["review_pause"] > 0:
            await asyncio.sleep(seq["review_pause"])

        # 6. Send Bubble
        formatted_chunk = format_message(chunk)
        await send_message(to, formatted_chunk)


async def send_template_message(to: str, template_name: str, language_code: str = "en", components: list = None) -> dict | None:
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
    """
    Send a typing indicator using the correct WhatsApp Cloud API format.
    Requires a message_id (the incoming message to mark as read + show typing).
    Auto-dismisses after 25 seconds or when next message is sent.
    """
    if not message_id:
        logger.debug(f"[Typing] Skipped — no message_id provided for {to}")
        return False

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {
            "type": "text"
        }
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{BASE_URL}/messages", headers=_headers(), json=payload)
            if resp.status_code == 200:
                logger.info(f"[Typing] ✍️ Indicator sent for {to}")
                return True
            else:
                logger.debug(f"[Typing] Failed: {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        logger.error(f"[Typing] Error: {e}")
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
