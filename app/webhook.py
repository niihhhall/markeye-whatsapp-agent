import asyncio
import logging
from fastapi import APIRouter, Request, Response, BackgroundTasks
from app.config import settings
from app.redis_client import redis_client
from app.whatsapp_cloud_client import mark_as_read
from app.models import ConversationState

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_INTERRUPT_RETRIES = 2

# ═══ WEBHOOK VERIFICATION (GET) ═══

@router.get("/webhook")
async def verify_webhook(request: Request):
    """Meta sends GET to verify webhook URL during setup."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Reachable", media_type="text/plain")

# ═══ WEBHOOK HANDLER (POST) ═══

@router.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive incoming WhatsApp Cloud API webhook.
    NEVER process immediately. Always buffer first.
    Return 200 instantly — process async.
    """
    try:
        payload = await request.json()
        
        # Ignore non-whatsapp events
        if payload.get("object") != "whatsapp_business_account":
            return {"status": "ignored"}
        
        # Extract message data from nested structure
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # IMPORTANT: Ignore status updates (delivered, read receipts)
        # Only process actual messages
        messages = value.get("messages")
        if not messages:
            return {"status": "ok"}  # Status update, not a message
        
        message = messages[0]
        contacts = value.get("contacts", [{}])
        contact = contacts[0] if contacts else {}
        
        # Extract fields
        sender_phone_raw = message.get("from", "")         # "447700900000"
        message_id = message.get("id", "")                  # "wamid.xxx"
        message_type = message.get("type", "")               # "text", "audio", etc.
        sender_name = contact.get("profile", {}).get("name", "")
        
        # Convert to internal format
        cleaned = sender_phone_raw.strip().lstrip("+")
        sender_phone = f"whatsapp:+{cleaned}"
        
        # Dedup check
        if message_id and await redis_client.check_dedup(message_id):
            return {"status": "duplicate"}
        
        # ═══ EXTRACT MESSAGE TEXT ═══
        if message_type == "text":
            message_text = message.get("text", {}).get("body", "")
            
        elif message_type == "audio":
            # Voice note — needs STT transcription
            from app.stt import process_voice_note
            from app.whatsapp_cloud_client import get_media_url, _get_headers
            media_id = message.get("audio", {}).get("id", "")
            media_url = await get_media_url(media_id)
            if media_url:
                message_text = await process_voice_note(media_url, headers=_get_headers())
            else:
                message_text = ""
                
            if not message_text:
                # Transcription failed
                from app.whatsapp_cloud_client import send_message
                await send_message(sender_phone, "Sorry, had trouble hearing that. Mind typing it out?")
                return {"status": "stt_failed"}
        else:
            # Image, document, sticker, location, etc. — ignore
            logger.info(f"Unsupported message type: {message_type}")
            return {"status": "ignored", "reason": f"type: {message_type}"}
        
        if not message_text:
            return {"status": "empty"}
        
        logger.info(f"Message from {sender_phone} ({sender_name}): {message_text[:80]}...")
        
        # State Checking and Tracker Logging before Buffering
        session = await redis_client.get_session(sender_phone)
        if not session:
            session = {"state": ConversationState.OPENING, "history": [], "turn_count": 0}

        # 1. CLOSED state — 24h cooldown guard
        state = session.get("state")
        if state == ConversationState.CLOSED:
            last_updated_str = session.get("last_updated")
            if last_updated_str:
                from datetime import datetime, timezone
                try:
                    last_updated = datetime.fromisoformat(last_updated_str.replace('Z', '+00:00'))
                    hours_since = (datetime.now(timezone.utc) - last_updated).total_seconds() / 3600
                    if hours_since < 24:
                        logger.info("[Webhook] Lead %s is CLOSED, ignoring. %.1fh remaining", sender_phone, 24 - hours_since)
                        return {"status": "ignored", "reason": "closed"}
                    else:
                        logger.info("[Webhook] Cooldown passed for %s, re-opening", sender_phone)
                        session["state"] = ConversationState.OPENING
                        await redis_client.save_session(sender_phone, session)
                except Exception as e:
                    logger.error("Error checking CLOSED cooldown: %s", e)
        
        # Tracker Logging
        try:
            from app.tracker import AlbertTracker
            tracker = AlbertTracker()
            lead = tracker.get_lead_by_phone(sender_phone)
            if not lead:
                lead = tracker.create_lead(phone=sender_phone, first_name=sender_name)
            if lead:
                tracker.log_inbound(lead["id"], message_text)
        except Exception as e:
            logger.error("[Webhook] Tracker failed: %s", e)

        # 4. Check for low-content spam (WAITING transition)
        from app.conversation import check_low_content
        is_spam_threshold_reached = await check_low_content(sender_phone, message_text, session)
        if is_spam_threshold_reached:
            return {"status": "ok", "action": "entered_waiting"}

        # ═══ BUFFER — DON'T PROCESS YET ═══
        batch_id = await redis_client.buffer_message(sender_phone, message_text)
        
        # Mark as read (blue ticks) — do this immediately
        background_tasks.add_task(
            _delayed_read_receipt, message_id
        )
        
        # Start buffer timer
        background_tasks.add_task(
            _delayed_buffer_process, sender_phone, batch_id, message_id
        )
        
        # Safety: hard max timer
        background_tasks.add_task(
            _hard_max_check, sender_phone, message_id
        )
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error"}

# ═══ DELAYED READ RECEIPT ═══

async def _delayed_read_receipt(message_id: str):
    """Send blue ticks after a short natural delay."""
    try:
        await asyncio.sleep(2.0)
        await mark_as_read(message_id)
    except Exception as e:
        logger.error(f"Read receipt error: {e}")

# ═══ BUFFER TIMER (3-second rolling) ═══

async def _delayed_buffer_process(phone: str, batch_id: str, message_id: str):
    """
    Wait 3 seconds. If no new messages arrived (batch still current),
    process the buffer. If new message arrived, this timer dies silently.
    """
    await asyncio.sleep(settings.INPUT_BUFFER_SECONDS)
    
    # Is this still the current batch?
    if not await redis_client.is_batch_current(phone, batch_id):
        return  # Newer message arrived — newer timer will handle it
    
    # Don't start processing if already generating (interrupt handler will pick up)
    if await redis_client.is_generating(phone):
        logger.info(f"Generation in progress for {phone}, interrupt will handle")
        return
    
    combined_text = await redis_client.get_and_clear_buffer(phone)
    if combined_text:
        logger.info(f"Buffer ready for {phone}: {combined_text[:80]}...")
        asyncio.create_task(
            _process_with_interrupt(phone, combined_text, message_id=message_id)
        )

# ═══ HARD MAX TIMER (8-second safety cap) ═══

async def _hard_max_check(phone: str, message_id: str):
    """Force-process after 8 seconds even if messages keep arriving."""
    await asyncio.sleep(settings.INPUT_BUFFER_MAX_SECONDS)
    
    # Only if buffer hasn't been processed yet
    if await redis_client.is_generating(phone):
        return  # Already processing
    
    # Check if we hit the hard max based on first message
    if not await redis_client.has_hit_hard_max(phone):
        return
        
    combined_text = await redis_client.get_and_clear_buffer(phone)
    if combined_text:
        logger.info(f"Hard max (8s) for {phone}, force-processing")
        asyncio.create_task(
            _process_with_interrupt(phone, combined_text, message_id=message_id)
        )

# ═══ MAIN PROCESSING WITH INTERRUPT PROTECTION ═══

async def _process_with_interrupt(
    phone: str, 
    combined_text: str, 
    retry_count: int = 0,
    message_id: str = ""
):
    """
    Generate reply with interrupt protection by passing to conversation engine.
    If new messages arrive during LLM generation -> discard -> re-read -> re-generate.
    """
    from app.conversation import process_conversation
    
    try:
        # Note: We rely on the process_conversation method internally checking states,
        # but to fulfill the master prompt's exact flow, we wrap it here.
        # process_conversation internally sets `processing` flag, 
        # and has its own interrupt check. We will let `process_conversation` handle 
        # the LLM call as it includes Tracker, Postgres, Calendly logic.
        
        # We wrap standard process_conversation with generating states
        await redis_client.set_generating(phone)
        
        # Call the main conversation engine. It does its own specific operations.
        # Since process_conversation also checks for interruptions natively (modified in conversation.py),
        # we defer to it.
        await process_conversation(phone, combined_text, conversation_id="", message_id=message_id)
        
        await redis_client.clear_generating(phone)
        
    except Exception as e:
        logger.error(f"Processing error for {phone}: {e}")
        await redis_client.clear_generating(phone)

@router.post("/admin/reset-session")
async def admin_reset_session(request: Request):
    """Admin endpoint to reset a lead's session"""
    try:
        body = await request.json()
        phone = body.get("phone", "").strip()
        if not phone:
            return {"status": "error", "reason": "phone is required"}
        await redis_client.redis.delete(f"session:{phone}")
        await redis_client.redis.delete(f"buffer:{phone}")
        await redis_client.redis.delete(f"batch:{phone}")
        await redis_client.redis.delete(f"calendly_sent:{phone}")
        await redis_client.redis.delete(f"low_content:{phone}")
        await redis_client.redis.delete(f"generating:{phone}")
        logger.info("[Admin] 🔄 Session reset for %s", phone)
        return {"status": "ok", "message": f"Session reset for {phone}."}
    except Exception as e:
        logger.error("[Admin] Reset failed: %s", e)
        return {"status": "error", "reason": str(e)}

@router.get("/admin/leads")
async def admin_get_all_leads():
    try:
        from app.tracker import AlbertTracker
        tracker = AlbertTracker()
        leads = tracker.get_all_leads()
        return {"status": "ok", "count": len(leads), "leads": leads}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/admin/lead-status/{phone}")
async def admin_get_lead_status(phone: str):
    try:
        session = await redis_client.get_session(phone)
        from app.tracker import AlbertTracker
        from app.supabase_client import supabase_client
        tracker = AlbertTracker()
        lead = tracker.get_lead_by_phone(phone)
        if not lead:
            return {"status": "error", "message": "Lead not found in Supabase database"}
            
        res = supabase_client.client.table("conversation_state").select("*").eq("lead_id", lead["id"]).execute()
        conv_state = res.data[0] if res.data else {}
        
        return {
            "phone": phone,
            "lead": {
                "id": lead.get("id"),
                "name": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or "Unknown",
                "score": lead.get("signal_score", 0),
                "temperature": lead.get("temperature", "Cold"),
            },
            "status": {
                "redis_state": session.get("state") if session else "NOT_IN_REDIS",
                "db_state": conv_state.get("current_state", "None"),
            },
            "bant_signals": {
                "budget": conv_state.get("bant_budget"),
                "timeline": conv_state.get("bant_timeline"),
            },
            "recent_chat_history": session.get("history", []) if session else []
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
