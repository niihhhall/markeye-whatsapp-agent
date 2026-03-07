import json
import logging
import asyncio
from fastapi import APIRouter, Request, BackgroundTasks
from app.config import settings
from app.redis_client import redis_client
from app.conversation import process_conversation
from app.messagebird_client import get_contact_phone, _to_internal_phone

logger = logging.getLogger(__name__)
router = APIRouter()


async def _buffer_timeout_handler(phone: str):
    """Waits for input buffer to expire, then processes combined message."""
    await asyncio.sleep(settings.INPUT_BUFFER_SECONDS)

    while await redis_client.is_timer_active(phone):
        await asyncio.sleep(0.5)

    messages = await redis_client.get_and_clear_buffer(phone)
    if messages:
        combined_message = " ".join(messages)
        await process_conversation(phone, combined_message)


@router.post("/webhook")
async def bird_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives Bird (formerly MessageBird) Channels webhook (JSON).

    Bird webhook payload for whatsapp.inbound:
    {
      "event": "whatsapp.inbound",
      "message": {
        "id": "...",
        "channelId": "...",
        "direction": "incoming",        <- may or may not be present
        "sender": {
          "contact": {
            "id": "...",
            "identifierKey": "phonenumber",
            "identifierValue": "+918160178327"
          }
        },
        "body": {
          "type": "text",
          "text": { "text": "Hello" }
        }
      }
    }
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("Webhook: non-JSON body received")
        return {"status": "error", "reason": "invalid_json"}

    # Temporarily log at WARNING so it shows in Railway logs for debugging
    logger.warning("Bird webhook payload: %s", json.dumps(payload)[:800])

    event = payload.get("event", payload.get("type", ""))
    logger.info("Bird webhook event: %s", event)

    # Only handle inbound events (we registered specifically for whatsapp.inbound)
    # Also accept empty event string as fallback since we only subscribed to inbound
    if event and not event.endswith(".inbound"):
        logger.info("Ignoring non-inbound event: %s", event)
        return {"status": "ignored", "reason": f"event:{event}"}

    # Bird Channels API: message is nested under "payload" key
    # Structure: {"service": "channels", "event": "whatsapp.inbound", "payload": {...}}
    message = payload.get("payload", payload)  # fallback to root if no "payload" key

    message_id = message.get("id", "")

    # ── Extract sender phone ────────────────────────────────────────────────
    # Bird sends: message.sender.contact.identifierValue
    sender_obj  = message.get("sender", {})
    contact_obj = sender_obj.get("contact", {})
    identifier  = contact_obj.get("identifierValue", "")
    contact_id  = contact_obj.get("id", "")

    sender_phone = None
    if identifier:
        sender_phone = _to_internal_phone(identifier)
        logger.info("Phone from identifierValue: %s", sender_phone)
    elif contact_id:
        sender_phone = await get_contact_phone(contact_id)
        logger.info("Phone from Contacts API: %s", sender_phone)

    if not sender_phone:
        logger.error(
            "Could not resolve phone. sender=%s contact=%s",
            sender_obj, contact_obj
        )
        return {"status": "error", "reason": "phone_resolution_failed"}

    # ── Extract message text ────────────────────────────────────────────────
    body_obj = message.get("body", {})
    msg_type = body_obj.get("type", "")

    if msg_type == "text":
        message_text = body_obj.get("text", {}).get("text", "")
    elif msg_type == "":
        # Fallback: try reading text directly (some older Bird formats)
        message_text = message.get("text", {}).get("text", "") or message.get("content", {}).get("text", "")
    else:
        logger.info("Unsupported message type: %s", msg_type)
        return {"status": "ignored", "reason": f"unsupported_type:{msg_type}"}

    if not message_text:
        logger.warning("Empty message body from %s", sender_phone)
        return {"status": "ignored", "reason": "empty_body"}

    # ── Deduplication ───────────────────────────────────────────────────────
    if message_id and await redis_client.check_dedup(message_id):
        logger.info("Duplicate message %s, ignoring", message_id)
        return {"status": "ignored", "reason": "duplicate"}

    logger.info("Bird inbound from %s: %.80s…", sender_phone, message_text)

    # ── Buffer + schedule ───────────────────────────────────────────────────
    await redis_client.buffer_message(sender_phone, message_text)
    await redis_client.set_buffer_timer(sender_phone)

    background_tasks.add_task(_buffer_timeout_handler, sender_phone)

    return {"status": "ok"}
