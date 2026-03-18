import asyncio
import logging
import random
from fastapi import APIRouter, Request, Response, BackgroundTasks
from app.config import settings
from app.redis_client import redis_client
from app.messaging import mark_as_read, send_message, send_chunked_messages
from app.models import ConversationState
from app.stt import process_voice_note_from_media_id

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_INTERRUPT_RETRIES = 2

@router.get("/webhook")
async def verify_webhook(request: Request):
    """WhatsApp Cloud API webhook verification (GET request)."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Reachable", status_code=200 if not mode else 403)


@router.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive incoming WhatsApp messages via Cloud API webhook.
    NEVER process immediately. Always buffer first.
    Return 200 instantly — process async.
    """
    try:
        payload = await request.json()
        
        # Ignore non-WhatsApp events
        if payload.get("object") != "whatsapp_business_account":
            return {"status": "ignored"}
        
        # Extract data from nested structure
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Check if this is a message (not a status update)
        if "messages" not in value:
            # Check for status updates (delivered, read, failed)
            if "statuses" in value:
                status_obj = value["statuses"][0]
                logger.info(f"[Webhook Status] {status_obj.get('recipient_id')}: {status_obj.get('status')}")
            return {"status": "ignored"}
        
        message = value["messages"][0]
        contact = value.get("contacts", [{}])[0]
        metadata = value.get("metadata", {})
        
        # Extract fields
        sender_wa_id = message.get("from", "")       # "447700900000"
        sender_name = contact.get("profile", {}).get("name", "")
        message_id = message.get("id", "")            # "wamid.xxx"
        message_type = message.get("type", "")        # "text" | "audio" | etc.
        message_ts = int(message.get("timestamp", 0))
        
        # Convert to internal phone format
        sender_phone = f"whatsapp:+{sender_wa_id}"    # "whatsapp:+447700900000"
        
        # 1. Dedup Check (Safeguard 2)
        dedup_key = f"dedup:{message_id}"
        if await redis_client.redis.get(dedup_key):
            logger.info(f"Duplicate message {message_id}, ignoring")
            return {"status": "duplicate"}
        await redis_client.redis.set(dedup_key, "1", ex=86400)

        # 2. Staleness check (Safeguard 1)
        import time
        message_age = int(time.time()) - message_ts
        if message_ts > 0 and message_age > 300:
            logger.info(f"Stale message ignored from {sender_phone}, age: {message_age}s")
            return {"status": "ignored", "reason": "stale"}

        # 3. Generation Cleanup (Safeguard 3)
        await redis_client.check_and_clear_stale_generation(sender_phone)

        # 4. CLOSED State Check (Master Prompt Fix 4)
        session = await redis_client.get_session(sender_phone)
        if session and session.get("state") == ConversationState.CLOSED:
            # Check 24h cooldown
            last_updated = session.get("last_updated")
            if last_updated:
                from datetime import datetime
                try:
                    lu_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                    diff = (datetime.utcnow().replace(tzinfo=None) - lu_dt.replace(tzinfo=None)).total_seconds()
                    if diff < 86400: # 24 hours
                        logger.info(f"[Webhook] {sender_phone} is CLOSED. Ignoring message.")
                        return {"status": "ignored", "reason": "closed_state"}
                except: pass
        
        # 3. Dedup check
        
        # === EXTRACT MESSAGE TEXT (text or voice note) ===
        message_text = ""
        
        if message_type == "text":
            message_text = message.get("text", {}).get("body", "")
            
        elif message_type == "audio":
            # Voice note — optional acknowledgment
            if settings.VOICE_NOTE_ACKNOWLEDGE and settings.VOICE_NOTE_ACK_MESSAGE:
                await send_message(sender_phone, settings.VOICE_NOTE_ACK_MESSAGE)
                
            # Download and transcribe
            audio_media_id = message.get("audio", {}).get("id", "")
            if audio_media_id:
                message_text = await process_voice_note_from_media_id(audio_media_id)
                if not message_text:
                    # Transcription failed fallback
                    if settings.VOICE_NOTE_ACKNOWLEDGE:
                        await send_message(sender_phone, 
                            "Sorry, I had trouble hearing that voice note. Mind typing it out for me?")
                    return {"status": "error", "reason": "transcription failed"}
            else:
                return {"status": "error", "reason": "missing audio media id"}
                
        elif message_type == "document" or message_type == "image":
            # Check for audio files sent as documents (Bird new API or direct uploads)
            doc_mime = message.get(message_type, {}).get("mime_type", "")
            if doc_mime.startswith("audio/"):
                doc_media_id = message.get(message_type, {}).get("id", "")
                if doc_media_id:
                    message_text = await process_voice_note_from_media_id(doc_media_id)
                    if not message_text:
                        if settings.VOICE_NOTE_ACKNOWLEDGE:
                           await send_message(sender_phone, 
                                "Sorry, I had trouble hearing that voice note. Mind typing it out for me?")
                        return {"status": "error", "reason": "transcription failed"}
            else:
                logger.info(f"Ignored non-audio file: {message_type}")
                return {"status": "ignored", "reason": f"unsupported type: {message_type}"}
        else:
            # Unsupported type (image, sticker, location, etc.)
            logger.info(f"Unsupported message type: {message_type}")
            return {"status": "ignored", "reason": f"unsupported type: {message_type}"}
        
        if not message_text:
            return {"status": "ignored", "reason": "empty message"}
        
        logger.info(f"Message from {sender_phone} ({sender_name}): {message_text[:80]}...")
        
        # === ADMIN TRAINING COMMANDS ===
        from app.training_handler import training_handler
        if training_handler.is_training_command(message_text) and training_handler.is_admin(sender_phone):
            logger.info(f"[Training] Admin command from {sender_phone}")
            response = await training_handler.handle(sender_phone, message_text)
            if response:
                await send_message(sender_phone, response)
                return {"status": "ok", "admin": "handled"}

        # === BUFFER THE MESSAGE — DON'T PROCESS YET ===
        batch_id = await redis_client.buffer_message(sender_phone, message_text)
        
        # Store last message_id and sender_name for processing
        await redis_client.redis.set(f"last_msg_id:{sender_phone}", message_id, ex=300)
        await redis_client.redis.set(f"last_name:{sender_phone}", sender_name, ex=300)
        
        # Fire delayed processor (3s rolling timer)
        background_tasks.add_task(_delayed_buffer_process, sender_phone, batch_id)
        
        # Fire hard-max safety check (8s fixed timer from first message in batch)
        # We only start this if it's the first message of a potentially new batch
        if await redis_client.redis.get(f"buffer_first:{sender_phone}"):
            # Already running for this batch
            pass
        else:
            # This shouldn't happen because buffer_message sets it, but for safety:
            background_tasks.add_task(_hard_max_check, sender_phone)
        
        # Tracker Log in background
        background_tasks.add_task(_background_tracker_log, sender_phone, sender_name, message_text)
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return {"status": "error"}


async def _background_tracker_log(phone: str, name: str, message: str):
    """Logs incoming message to Supabase in the background."""
    try:
        from app.tracker import AlbertTracker
        tracker = AlbertTracker()
        lead = await tracker.get_lead_by_phone(phone)
        if not lead:
            lead = await tracker.create_lead(phone=phone, first_name=name)
        if lead:
            await tracker.log_inbound(lead["id"], message)
    except Exception as e:
        logger.error("[Webhook] Background Tracker failed: %s", e)


async def _delayed_buffer_process(phone: str, batch_id: str):
    """
    Wait. If no new messages arrived (batch_id still current),
    process the buffer. If new message arrived, this timer dies silently.
    """
    await asyncio.sleep(settings.INPUT_BUFFER_SECONDS)
    
    # Is this still the current batch?
    if not await redis_client.is_batch_current(phone, batch_id):
        return  # Newer message arrived, a newer timer will handle it
    
    # Clean up any stuck generation flags
    await redis_client.check_and_clear_stale_generation(phone)
    
    # If LLM generation already in progress, don't start another
    if await redis_client.is_generating(phone):
        return  # The interrupt handler will pick up new messages
    
    combined = await redis_client.get_and_clear_buffer(phone)
    if combined:
        logger.info(f"Buffer ready for {phone}: {combined[:80]}...")
        asyncio.create_task(_process_with_interrupt_protection(phone, combined))


async def _hard_max_check(phone: str):
    """Hard max safety — force process even if messages still arriving."""
    await asyncio.sleep(settings.INPUT_BUFFER_MAX_SECONDS)
    
    # Only if buffer still has unprocessed content
    if await redis_client.has_hit_hard_max(phone):
        if await redis_client.is_generating(phone):
            return  # Already processing, interrupt handler will catch it
        
        combined = await redis_client.get_and_clear_buffer(phone)
        if combined:
            logger.info(f"Hard max hit for {phone}, force-processing")
            asyncio.create_task(_process_with_interrupt_protection(phone, combined))


async def _process_with_interrupt_protection(
    phone: str, 
    combined_text: str, 
    retry_count: int = 0
):
    """
    Generate reply with interrupt protection.
    If new messages arrive during LLM generation, discard stale response
    and re-generate with full combined context.
    """
    from app.conversation import process_conversation
    
    try:
        # 1. State check (CLOSED state handler)
        session = await redis_client.get_session(phone)
        if session and session.get("state") == ConversationState.CLOSED:
            # Handled in webhook for instant rejection, but here for safety
            # But wait, we want to allow re-opening if cooldown passed.
            # We'll skip complex cooldown check here and let conversation engine handle or just reject.
            pass

        if last_msg_id:
            await asyncio.sleep(random.uniform(1, 4))
            # conversation_id not strictly needed for Cloud but required by proxy
            await mark_as_read("", last_msg_id) 
        
        # 3. Mark generation in progress
        await redis_client.set_generating(phone)
        
        # 4. Process via conversation engine
        # We need to handle the interrupt check within or around the conversation call.
        # The Master Prompt suggests a flow where we call generate_response directly,
        # but our app has a complex process_conversation.
        # We will wrap it.
        
        # To strictly follow "Interrupt Protection", we need to check `has_new_messages` 
        # AFTER the LLM potentially returns. 
        # However, process_conversation is a black box that also sends messages.
        
        # OPTION: Modify process_conversation to return the response text INSTEAD of sending,
        # but that's a big change.
        # BETTER: Use the existing logic in process_conversation which ALREADY has 
        # an interrupt check (Step 9 in conversation.py).
        
        await process_conversation(phone, combined_text, message_id=last_msg_id or "")
        
        # 5. Interrupt Check (Layer 3)
        # Note: Step 9 in conversation.py already does this:
        # "If new messages arrived during processing for ..., re-generating"
        # It recursively calls itself.
        
        await redis_client.clear_generating(phone)
        
    except Exception as e:
        logger.error(f"Processing error for {phone}: {e}", exc_info=True)
        await redis_client.clear_generating(phone)

# Admin endpoints... (Keeping them)
@router.post("/admin/reset-session")
async def admin_reset_session(request: Request):
    try:
        body = await request.json()
        phone = body.get("phone", "").strip()
        if not phone: return {"status": "error"}
        await redis_client.redis.delete(f"session:{phone}")
        await redis_client.redis.delete(f"buffer:{phone}")
        await redis_client.redis.delete(f"buffer_batch:{phone}")
        await redis_client.redis.delete(f"generating:{phone}")
        return {"status": "ok"}
    except: return {"status": "error"}
