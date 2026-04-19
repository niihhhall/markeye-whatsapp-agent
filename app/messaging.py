import logging
import json
from app.config import settings
from app.redis_client import redis_client
from app import whatsapp_client as cloud

logger = logging.getLogger(__name__)

async def send_message(to: str, body: str, client_id: str = None) -> dict | None:
    """Send a message using the active provider."""
    provider = settings.MESSAGING_PROVIDER
    
    if provider == "baileys":
        logger.info(f"[Messaging] Publishing outbound to Baileys ({client_id}): {to}")
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net",
            "message": body,
            "replyToMessageId": await redis_client.redis.get(f"last_msg_id:{to}")
        }
        await redis_client.redis.publish("outbound", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys"}
    
    return await cloud.send_message(to, body)

async def send_media(to: str, media_type: str, url: str, caption: str = "", client_id: str = None) -> dict | None:
    """Send media (image, document, audio) using Baileys."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net",
            "type": media_type,
            "url": url,
            "caption": caption
        }
        await redis_client.redis.publish("outbound:media", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys", "type": media_type}
    return None

async def send_reaction(to: str, emoji: str, original_msg_id: str) -> dict | None:
    """Send a reaction using Baileys."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "to": f"{phone_id}@s.whatsapp.net",
            "emoji": emoji,
            "originalMessageKey": {
                "remoteJid": f"{phone_id}@s.whatsapp.net",
                "id": original_msg_id,
                "fromMe": False
            }
        }
        await redis_client.redis.publish("outbound:reaction", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys"}
    return None

async def mark_as_read(conversation_id: str, message_id: str) -> bool:
    """Mark a message as read."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        if not message_id: return False
        
        # Parse conversation id (phone)
        phone_id = conversation_id.split(':')[-1] if ':' in conversation_id else conversation_id
        payload = {
            "to": f"{phone_id}@s.whatsapp.net",
            "messageId": message_id
        }
        await redis_client.redis.publish("outbound:mark_read", json.dumps(payload))
        return True
    return await cloud.mark_as_read(message_id)

async def send_chunked_messages(
    to: str, 
    chunks: list[str], 
    incoming_text: str = "", 
    last_message_ts: float = 0, 
    message_id: str = "",
    client_id: str = None
) -> None:
    """Send chunked messages with delays."""
    import asyncio
    provider = settings.MESSAGING_PROVIDER
    
    if provider == "baileys":
        for idx, chunk in enumerate(chunks):
            await send_message(to, chunk, client_id=client_id)
            if idx < len(chunks) - 1:
                await send_typing_indicator(to, client_id=client_id)
                await asyncio.sleep(settings.CHUNK_DELAY_SECONDS)
        return

    return await cloud.send_chunked_messages(to, chunks, incoming_text, last_message_ts, message_id)

async def send_typing_indicator(to: str, conversation_id: str = "", message_id: str = "", client_id: str = None) -> bool:
    """Send typing indicator."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net"
        }
        await redis_client.redis.publish("outbound:typing", json.dumps(payload))
        return True
    return await cloud.send_typing_indicator(to, message_id)

async def send_poll(to: str, question: str, options: list[str], client_id: str = None) -> dict | None:
    """Send a poll using Baileys."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net",
            "question": question,
            "options": options
        }
        await redis_client.redis.publish("outbound:poll", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys", "type": "poll"}
    return None

async def edit_message(to: str, message_id: str, new_text: str) -> dict | None:
    """Edit a previously sent message."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "to": f"{phone_id}@s.whatsapp.net",
            "messageId": message_id,
            "newText": new_text
        }
        await redis_client.redis.publish("outbound:edit", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys", "type": "edit"}
    return None

async def delete_message(to: str, message_id: str) -> dict | None:
    """Delete a previously sent message."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "to": f"{phone_id}@s.whatsapp.net",
            "messageId": message_id
        }
        await redis_client.redis.publish("outbound:delete", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys", "type": "delete"}
    return None

async def forward_message(to: str, original_msg_id: str, forward_to: str) -> dict | None:
    """Forward a message to another contact."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        target_id = forward_to.split(':')[-1] if ':' in forward_to else forward_to
        payload = {
            "to": f"{phone_id}@s.whatsapp.net",
            "forwardTo": f"{target_id}@s.whatsapp.net",
            "originalMessageKey": {
                "remoteJid": f"{phone_id}@s.whatsapp.net",
                "id": original_msg_id,
                "fromMe": False
            }
        }
        await redis_client.redis.publish("outbound:forward", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys", "type": "forward"}
    return None

async def send_contact(to: str, contact_name: str, contact_phone: str) -> dict | None:
    """Send a contact card."""
    provider = settings.MESSAGING_PROVIDER
    if provider == "baileys":
        phone_id = to.split(':')[-1] if ':' in to else to
        payload = {
            "to": f"{phone_id}@s.whatsapp.net",
            "contactName": contact_name,
            "contactPhone": contact_phone
        }
        await redis_client.redis.publish("outbound:contact", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys", "type": "contact"}
    return None

async def get_contact_phone(contact_id: str) -> str | None:
    return None
