import json
import logging
import asyncio
from fastapi import APIRouter, Request, BackgroundTasks
from app.config import settings
from app.redis_client import redis_client
from app.conversation import process_conversation
from app.messagebird_client import get_contact_phone, _to_internal_phone, send_message, mark_as_read
from app.stt import process_voice_note

logger = logging.getLogger(__name__)
router = APIRouter()


async def _buffer_timeout_handler(phone: str, last_message_id: str = ""):
    """Waits for input buffer to expire, then processes combined message."""
    print(f"[Webhook] _buffer_timeout_handler started for {phone}", flush=True)
    # Simulation: Someone "opens" the notification after a short delay
    if last_message_id and settings.MARK_AS_READ_DELAY > 0:
        await asyncio.sleep(settings.MARK_AS_READ_DELAY)
        await mark_as_read(last_message_id)

    # Wait for the full input buffer window (configured in settings)
    await asyncio.sleep(settings.INPUT_BUFFER_SECONDS - settings.MARK_AS_READ_DELAY if settings.MARK_AS_READ_DELAY < settings.INPUT_BUFFER_SECONDS else 0.5)

    while await redis_client.is_timer_active(phone):
        await asyncio.sleep(0.5)

    messages = await redis_client.get_and_clear_buffer(phone)
    if messages:
        combined_message = " ".join(messages)
        print(f"[Webhook] Processing combined message for {phone}: '{combined_message[:50]}...'", flush=True)
        # Use 'mixed' if any message was audio, but for simple buffer just default to text or pass from first
        await process_conversation(phone, combined_message)
    else:
        print(f"[Webhook] No buffered messages for {phone}", flush=True)


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

    # Fallback for some Bird v2 formats where phone is in contact.key
    if not sender_phone:
        sender_phone = contact_obj.get("key")
        if sender_phone:
            sender_phone = _to_internal_phone(sender_phone)
            logger.info("Phone from contact.key fallback: %s", sender_phone)

    if not sender_phone:
        logger.error(
            "Could not resolve phone. Full message payload: %s",
            json.dumps(message)
        )
        return {"status": "error", "reason": "phone_resolution_failed"}

    # ── Extract message text ────────────────────────────────────────────────
    body_obj = message.get("body", {})
    msg_type = body_obj.get("type", "text")
    message_text = ""
    message_source = "text"

    if msg_type == "text":
        message_text = body_obj.get("text", {}).get("text", "")
    elif msg_type == "audio":
        audio_url = body_obj.get("audio", {}).get("url", "")
        if not audio_url:
            logger.warning("Audio message received but no URL found")
            return {"status": "error", "reason": "no_audio_url"}
        
        # Optional: acknowledge voice note receipt
        if settings.VOICE_NOTE_ACKNOWLEDGE and settings.VOICE_NOTE_ACK_MESSAGE:
            await send_message(sender_phone, settings.VOICE_NOTE_ACK_MESSAGE)
        
        # Transcribe
        message_text = await process_voice_note(audio_url)
        message_source = "audio"

        if not message_text:
            if settings.VOICE_NOTE_ACKNOWLEDGE:
                await send_message(sender_phone, "Sorry, I had trouble hearing that voice note. Mind typing it out for me?")
            return {"status": "error", "reason": "transcription_failed"}

    elif msg_type == "file":
        # Handle Bird (new API) format where audio comes as file type
        files = body_obj.get("file", {}).get("files", [])
        audio_file = None
        for f in files:
            content_type = f.get("contentType", "")
            if content_type and content_type.startswith("audio/"):
                audio_file = f
                break
        
        if audio_file:
            audio_url = audio_file.get("mediaUrl", "") or audio_file.get("url", "")
            if audio_url:
                # Optional: acknowledge
                if settings.VOICE_NOTE_ACKNOWLEDGE and settings.VOICE_NOTE_ACK_MESSAGE:
                    await send_message(sender_phone, settings.VOICE_NOTE_ACK_MESSAGE)
                
                message_text = await process_voice_note(audio_url)
                message_source = "audio"
                if not message_text:
                    if settings.VOICE_NOTE_ACKNOWLEDGE:
                        await send_message(sender_phone, "Sorry, I had trouble hearing that voice note. Mind typing it out for me?")
                    return {"status": "error", "reason": "transcription_failed"}
            else:
                return {"status": "ignored", "reason": "no_audio_url_in_file"}
        else:
            logger.info("Received non-audio file message, ignoring")
            return {"status": "ignored", "reason": "unsupported_file_type"}

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

    # ── Tracking ────────────────────────────────────────────────────────────
    from app.tracker import AlbertTracker
    tracker = AlbertTracker()
    
    lead = tracker.get_lead_by_phone(sender_phone)
    if not lead:
        lead = tracker.create_lead(phone=sender_phone)
    
    if lead:
        tracker.log_inbound(lead["id"], message_text)

    logger.info("Bird inbound from %s: %.80s…", sender_phone, message_text)

    # ── Buffer + schedule ───────────────────────────────────────────────────
    await redis_client.buffer_message(sender_phone, message_text)
    await redis_client.set_buffer_timer(sender_phone)

    # Note: Currently buffer doesn't track source per fragment. 
    # If a voice note is part of a buffered sequence, it will be treated as text in combined.
    # We pass 'source' to process_conversation if we called it directly, 
    # but here it goes to buffer. For now, we'll just let it be.
    background_tasks.add_task(_buffer_timeout_handler, sender_phone, message_id)

    return {"status": "ok"}
