import logging
import asyncio
import random
import httpx
from app.config import settings
from app.chunker import calculate_typing_delay

logger = logging.getLogger(__name__)

def _get_base_url(config: dict) -> str:
    version = config.get("whatsapp_api_version", settings.WHATSAPP_API_VERSION or "v19.0")
    phone_id = config.get("whatsapp_phone_number_id", settings.WHATSAPP_PHONE_NUMBER_ID)
    return f"https://graph.facebook.com/{version}/{phone_id}"

def _get_headers(config: dict) -> dict:
    token = config.get("whatsapp_access_token", settings.WHATSAPP_ACCESS_TOKEN)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

async def send_message(to: str, body: str, client_config: Optional[dict] = None) -> dict | None:
    """Send a text message via WhatsApp Cloud API."""
    if not client_config:
        logger.error("[CloudAPI] No client_config provided for send_message")
        return None

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _to_wa_phone(to),
        "type": "text",
        "text": {"body": body}
    }
    
    url = f"{_get_base_url(client_config)}/messages"
    headers = _get_headers(client_config)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                logger.info(f"[CloudAPI] Sent to {to}: {body[:30]}...")
                return resp.json()
            else:
                logger.error(f"[CloudAPI] Send failed: {resp.status_code} — {resp.text}")
                return None
    except Exception as e:
        logger.error(f"[CloudAPI] Send error: {e}")
        return None


async def send_chunked_messages(
    to: str, 
    chunks: list[str], 
    incoming_text: str = "", 
    last_message_ts: float = 0, 
    message_id: str = "",
    interruptible: bool = True
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
            
            # Check for interrupts during typing (if interruptible)
            intervals = int(seq["typing_delay"] / 0.5)
            for _ in range(intervals):
                await asyncio.sleep(0.5)
                if interruptible and await redis_client.has_new_messages(to):
                    logger.info(f"Interrupt: New message during typing for {to}. Aborting.")
                    return
            await asyncio.sleep(seq["typing_delay"] % 0.5)

        # 5. Review Pause
        if seq["review_pause"] > 0:
            await asyncio.sleep(seq["review_pause"])

        # 6. Send Bubble
        formatted_chunk = format_message(chunk)
        await send_message(to, formatted_chunk)


async def send_template_message(to: str, template_name: str, language_code: str = "en", components: list = None, client_config: Optional[dict] = None) -> dict | None:
    """Send a WhatsApp template message."""
    if not client_config:
        return None

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

    url = f"{_get_base_url(client_config)}/messages"
    headers = _get_headers(client_config)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                logger.info(f"[CloudAPI] Template {template_name} sent to {to}")
                return resp.json()
            else:
                logger.error(f"[CloudAPI] Template failed: {resp.status_code} — {resp.text}")
                return None
    except Exception as e:
        logger.error(f"[CloudAPI] Template error: {e}")
        return None


async def send_typing_indicator(to: str, message_id: str = "", client_config: Optional[dict] = None) -> bool:
    """
    Send a typing indicator using the correct WhatsApp Cloud API format.
    """
    if not message_id or not client_config:
        logger.debug(f"[Typing] Skipped — missing id or config for {to}")
        return False

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"}
    }
    
    url = f"{_get_base_url(client_config)}/messages"
    headers = _get_headers(client_config)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"[Typing] Error: {e}")
        return False


async def mark_as_read(message_id: str, client_config: Optional[dict] = None) -> None:
    """Mark a message as read (blue ticks)."""
    if not message_id or not client_config:
        return

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    
    url = f"{_get_base_url(client_config)}/messages"
    headers = _get_headers(client_config)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, headers=headers, json=payload)
    except Exception as e:
        logger.error(f"Mark as read error: {e}")
