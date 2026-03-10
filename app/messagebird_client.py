import logging
import asyncio
import httpx
from app.config import settings
from app.chunker import calculate_typing_delay

logger = logging.getLogger(__name__)

# Bird (formerly MessageBird) API v2
BASE_URL = "https://api.bird.com"


def _get_headers() -> dict:
    """Return auth headers for Bird API."""
    return {
        "Authorization": f"AccessKey {settings.MESSAGEBIRD_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _to_bird_phone(phone: str) -> str:
    """
    Convert internal format to Bird format.
    Our system:  whatsapp:+447700900000
    Bird API:    +447700900000
    """
    return phone.replace("whatsapp:", "")


def _to_internal_phone(phone: str) -> str:
    """
    Convert Bird phone format to internal format.
    Bird:        +447700900000, 447700900000, or whatsapp:+44...
    Our system:  whatsapp:+447700900000
    """
    cleaned = str(phone).strip()
    # Remove whatsapp: prefix if already present to avoid double prefixing
    cleaned = cleaned.replace("whatsapp:", "")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return "whatsapp:" + cleaned


def _workspace_channel_url(path: str = "") -> str:
    """Build base URL for workspace+channel operations."""
    return (
        f"{BASE_URL}/workspaces/{settings.MESSAGEBIRD_WORKSPACE_ID}"
        f"/channels/{settings.MESSAGEBIRD_CHANNEL_ID}{path}"
    )


async def send_message(to: str, body: str) -> dict | None:
    """
    Send a WhatsApp message via Bird Channels API.

    Args:
        to:   Phone in our internal format (whatsapp:+XXXXXXXXXXX)
        body: Message text to send

    Returns:
        Parsed API response dict, or None on error.
    """
    bird_phone = _to_bird_phone(to)

    payload = {
        "receiver": {
            "contacts": [{"identifierValue": bird_phone}]
        },
        "body": {
            "type": "text",
            "text": {"text": body},
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info("Bird: sending to %s via URL: %s", bird_phone, _workspace_channel_url("/messages"))
            logger.info("Bird: payload: %s", json.dumps(payload))
            response = await client.post(
                _workspace_channel_url("/messages"),
                headers=_get_headers(),
                json=payload,
            )
            if response.status_code in (200, 201, 202):
                logger.info("Bird: message sent to %s: %.50s…", bird_phone, body)
                return response.json()
            else:
                logger.error(
                    "Bird send failed: %s — %s | Headers: %s",
                    response.status_code,
                    response.text,
                    response.headers
                )
                return None
    except Exception as exc:
        logger.error("Bird send error: %s", exc)
        return None


async def mark_as_read(message_id: str) -> bool:
    """
    Mark a WhatsApp message as read via Bird Channels API.
    
    Args:
        message_id: The ID of the message to mark as read.
        
    Returns:
        True if successful, False otherwise.
    """
    if not message_id:
        return False
        
    payload = {
        "status": "read"
    }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.patch(
                _workspace_channel_url(f"/messages/{message_id}"),
                headers=_get_headers(),
                json=payload,
            )
            if response.status_code in (200, 204):
                logger.debug("Bird: message %s marked as read", message_id)
                return True
            else:
                # Log as warning instead of error to reduce noise if it's a common 403/Forbidden
                logger.warning("Bird mark_as_read failed (likely permissions): %s — %s", response.status_code, response.text)
                return False
    except (httpx.RequestError, Exception) as exc:
        logger.warning("Bird mark_as_read connection error: %s", exc)
        return False


async def send_typing_indicator(to: str) -> bool:
    """
    Simulate a typing indicator. 
    Note: Bird Channels v2 may not have a dedicated typing endpoint, 
    so we might just log this for now or find the specific platform call.
    """
    if not settings.SHOW_TYPING_INDICATOR:
        return False
        
    bird_phone = _to_bird_phone(to)
    logger.debug("Bird: showing typing indicator for %s", bird_phone)
    
    # Placeholder for actual API call if supported by Bird v2
    # Many WhatsApp APIs use a 'typing' status in the messages endpoint
    return True


async def reply_to_conversation(conversation_id: str, body: str) -> dict | None:
    """
    Reply to a Bird conversation by ID.
    Falls back to send_message if no conversation_id given.
    """
    # Bird doesn't have a separate reply endpoint in v2 — use send_message
    # with the phone number retrieved from the session instead.
    # This function signature is kept for compatibility; callers should
    # prefer send_message(phone, body) when possible.
    logger.warning(
        "reply_to_conversation called with id=%s — Bird v2 has no reply endpoint; "
        "use send_message(phone, body) instead.",
        conversation_id,
    )
    return None


async def send_chunked_messages(to: str, chunks: list[str]) -> None:
    """
    Send multiple messages with realistic typing delays between them.

    Args:
        to:     Phone in our internal format (whatsapp:+XXXXXXXXXXX)
        chunks: Ordered list of message texts
    """
    for i, chunk in enumerate(chunks):
        if i > 0:
            # Show typing between chunks
            await send_typing_indicator(to)
            delay = calculate_typing_delay(chunk)
            await asyncio.sleep(delay)
        await send_message(to, chunk)


async def reply_chunked_messages(conversation_id: str, chunks: list[str]) -> None:
    """Kept for API compatibility — routes through send_message via phone."""
    logger.warning("reply_chunked_messages: Bird v2 has no reply endpoint.")


async def get_contact_phone(contact_id: str) -> str | None:
    """
    Fetch sender phone number from Bird Contacts API.
    Returns phone in internal format: whatsapp:+XXXXXXXXXXX
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{BASE_URL}/workspaces/{settings.MESSAGEBIRD_WORKSPACE_ID}/contacts/{contact_id}",
                headers=_get_headers(),
            )
            if response.status_code == 200:
                data = response.json()
                # identifierValue is the phone number in Bird v2
                identifier = data.get("identifierValue", "")
                if identifier:
                    return _to_internal_phone(identifier)
                # fallback: check identifiers array
                for ident in data.get("identifiers", []):
                    if ident.get("type") in ("phonenumber", "whatsapp"):
                        return _to_internal_phone(ident.get("key", ""))
            logger.warning("Bird: could not get phone for contact %s", contact_id)
            return None
    except Exception as exc:
        logger.error("Bird contact lookup error: %s", exc)
        return None
