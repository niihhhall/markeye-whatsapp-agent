import json
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
            # Using logger.info to see this in Railway logs
            logger.info("Bird: sending to %s via URL: %s", bird_phone, _workspace_channel_url("/messages"))
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
                    "Bird send failed: %s — %s",
                    response.status_code,
                    response.text
                )
                return None
    except Exception as exc:
        logger.error("Bird send error: %s", exc)
        return None


async def mark_as_read(conversation_id: str, message_id: str) -> bool:
    """
    Mark a WhatsApp message as read via Bird Conversations API.
    """
    if not conversation_id or not message_id:
        return False
        
    payload = {"status": "read"}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{BASE_URL}/workspaces/{settings.MESSAGEBIRD_WORKSPACE_ID}/conversations/{conversation_id}/messages/{message_id}"
            response = await client.patch(
                url,
                headers=_get_headers(),
                json=payload,
            )
            if response.status_code in (200, 204, 202):
                logger.info("Bird: message %s marked as read", message_id)
                return True
            if response.status_code == 403:
                logger.warning("Bird mark_as_read: 403 Forbidden. Check API key permissions for conversation.write")
                return False
            logger.warning("Bird mark_as_read failed: %s — %s", response.status_code, response.text)
            return False
    except Exception as exc:
        logger.error("Bird mark_as_read error: %s", exc)
        return False


async def send_typing_indicator(to: str, conversation_id: str = "") -> bool:
    """
    Simulated typing indicator. 
    Bird Channels API v2 currently doesn't support a dedicated 'typing' endpoint for WhatsApp.
    We simulate this by adding human-like delays in conversation.py.
    """
    return True


async def reply_to_conversation(conversation_id: str, body: str) -> dict | None:
    """Placeholder for API compatibility."""
    return None


async def send_chunked_messages(
    to: str, 
    chunks: list[str], 
    incoming_text: str = "", 
    last_message_ts: float = 0, 
    conversation_id: str = ""
) -> None:
    """
    Send multiple messages with realistic typing delays.
    """
    for i, chunk in enumerate(chunks):
        if i > 0:
            await send_typing_indicator(to, conversation_id)
            delay = calculate_typing_delay(chunk)
            await asyncio.sleep(delay)
        await send_message(to, chunk)


async def reply_chunked_messages(conversation_id: str, chunks: list[str]) -> None:
    """Placeholder for API compatibility."""
    pass


async def get_contact_phone(contact_id: str) -> str | None:
    """
    Fetch sender phone number from Bird Contacts API.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{BASE_URL}/workspaces/{settings.MESSAGEBIRD_WORKSPACE_ID}/contacts/{contact_id}",
                headers=_get_headers(),
            )
            if response.status_code == 200:
                data = response.json()
                identifier = data.get("identifierValue", "")
                if identifier:
                    return _to_internal_phone(identifier)
                for ident in data.get("identifiers", []):
                    if ident.get("type") in ("phonenumber", "whatsapp"):
                        return _to_internal_phone(ident.get("key", ""))
            return None
    except Exception as exc:
        logger.error("Bird contact lookup error: %s", exc)
        return None
