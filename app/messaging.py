import logging
from app.config import settings
from app import messagebird_client as bird
from app import whatsapp_cloud_client as cloud

logger = logging.getLogger(__name__)

async def send_message(to: str, body: str) -> dict | None:
    """Send a message using the configured provider."""
    if settings.MESSAGING_PROVIDER == "whatsapp_cloud":
        return await cloud.send_message(to, body)
    return await bird.send_message(to, body)

async def mark_as_read(conversation_id: str, message_id: str) -> bool:
    """Mark a message as read using the configured provider."""
    if settings.MESSAGING_PROVIDER == "whatsapp_cloud":
        # Meta only needs message_id
        return await cloud.mark_as_read(message_id)
    return await bird.mark_as_read(conversation_id, message_id)

async def send_chunked_messages(to: str, chunks: list[str], conversation_id: str = "", message_id: str = "") -> None:
    """Send chunked messages using the configured provider."""
    if settings.MESSAGING_PROVIDER == "whatsapp_cloud":
        return await cloud.send_chunked_messages(to, chunks, conversation_id, message_id)
    return await bird.send_chunked_messages(to, chunks, conversation_id)

async def send_typing_indicator(to: str, conversation_id: str = "", message_id: str = "") -> bool:
    """Send typing indicator using the configured provider."""
    if settings.MESSAGING_PROVIDER == "whatsapp_cloud":
        return await cloud.send_typing_indicator(to, message_id)
    return await bird.send_typing_indicator(to, conversation_id)

async def get_contact_phone(contact_id: str) -> str | None:
    """Search for contact phone (primarily MessageBird specific)."""
    if settings.MESSAGING_PROVIDER == "whatsapp_cloud":
        # Cloud API provides phone in the webhook directly
        return None
    return await bird.get_contact_phone(contact_id)
