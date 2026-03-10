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
    print(f"\n[Webhook] Raw request received at {datetime.now().isoformat()}", flush=True)
    try:
        try:
            payload = await request.json()
        except Exception:
            print("[Webhook] ❌ Failed to parse JSON body", flush=True)
            logger.warning("Webhook: non-JSON body received")
            return {"status": "error", "reason": "invalid_json"}

        # Temporarily log at WARNING so it shows in Railway logs for debugging
        print(f"[Webhook] Payload received: {json.dumps(payload)[:200]}...", flush=True)
        logger.warning("Bird webhook payload: %s", json.dumps(payload)[:1000])

        event = payload.get("event", payload.get("type", ""))
        logger.info("Bird webhook event: %s", event)

        if event and not event.endswith(".inbound"):
            logger.info("Ignoring non-inbound event: %s", event)
            return {"status": "ignored", "reason": f"event:{event}"}

        message = payload.get("payload", payload)
        message_id = message.get("id", "")

        # ── Extract sender phone ────────────────────────────────────────────────
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
            sender_phone = contact_obj.get("key")
            if sender_phone:
                sender_phone = _to_internal_phone(sender_phone)
                logger.info("Phone from contact.key fallback: %s", sender_phone)

        if not sender_phone:
            logger.error("Could not resolve phone. Full message payload: %s", json.dumps(message))
            return {"status": "error", "reason": "phone_resolution_failed"}

        # ── Extract message text ────────────────────────────────────────────────
        body_obj = message.get("body", {})
        msg_type = body_obj.get("type", "text")
        message_text = ""

        if msg_type == "text":
            message_text = body_obj.get("text", {}).get("text", "")
        elif msg_type == "audio":
            audio_url = body_obj.get("audio", {}).get("url", "")
            if audio_url:
                message_text = await process_voice_note(audio_url)
        elif msg_type == "file":
            files = body_obj.get("file", {}).get("files", [])
            for f in files:
                if f.get("contentType", "").startswith("audio/"):
                    audio_url = f.get("mediaUrl", "") or f.get("url", "")
                    if audio_url:
                        message_text = await process_voice_note(audio_url)
                    break
        elif msg_type == "":
            message_text = message.get("text", {}).get("text", "") or message.get("content", {}).get("text", "")

        if not message_text:
            logger.warning("Empty/Unsupported message body from %s", sender_phone)
            return {"status": "ignored", "reason": "empty_body"}

        # ── Redis Check ─────────────────────────────────────────────────────────
        logger.info("[Webhook] Redis ping check...")
        if not await redis_client.ping():
            logger.error("[Webhook] ❌ Redis is DOWN. Cannot buffer message.")

        if message_id and await redis_client.check_dedup(message_id):
            logger.info("Duplicate message %s, ignoring", message_id)
            return {"status": "ignored", "reason": "duplicate"}

        # ── Tracking ────────────────────────────────────────────────────────────
        try:
            from app.tracker import AlbertTracker
            tracker = AlbertTracker()
            lead = tracker.get_lead_by_phone(sender_phone)
            if not lead:
                lead = tracker.create_lead(phone=sender_phone)
            if lead:
                tracker.log_inbound(lead["id"], message_text)
                logger.info("[Webhook] ✅ Tracked inbound for lead %s", lead["id"])
        except Exception as e:
            logger.error("[Webhook] ❌ Tracker failed: %s", e)

        # ── Buffer + schedule ───────────────────────────────────────────────────
        logger.info("[Webhook] Buffering message for %s...", sender_phone)
        await redis_client.buffer_message(sender_phone, message_text)
        await redis_client.set_buffer_timer(sender_phone)
        
        logger.info("[Webhook] Scheduling _buffer_timeout_handler for %s", sender_phone)
        background_tasks.add_task(_buffer_timeout_handler, sender_phone, message_id)

        return {"status": "ok"}

    except Exception as e:
        logger.critical("[Webhook] 🚨 CRITICAL WEBHOOK FAILURE: %s", e, exc_info=True)
        return {"status": "error", "reason": str(e)}
