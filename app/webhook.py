import json
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Request, BackgroundTasks, Response, Query
from app.models import ConversationState
from app.config import settings
from app.redis_client import redis_client
from app.conversation import process_conversation
from app.messagebird_client import get_contact_phone as bird_get_contact, _to_internal_phone as bird_to_internal, send_message as bird_send, mark_as_read as bird_mark
from app.whatsapp_cloud_client import _to_internal_phone as cloud_to_internal
from app.stt import process_voice_note
from app.supabase_client import supabase_client
from app.tracker import AlbertTracker

logger = logging.getLogger(__name__)
router = APIRouter()


async def _buffer_timeout_handler(phone: str, batch_id: str, conversation_id: str = "", last_message_id: str = ""):
    """Waits for input buffer to expire (3s silence) or hard-max (8s), then processes."""
    logger.info("[Webhook] _buffer_timeout_handler started for %s (batch: %s)", phone, batch_id)
    
    # Wait for the rolling buffer window (3s)
    await asyncio.sleep(settings.INPUT_BUFFER_SECONDS)

    # Check if a newer batch has started
    current_batch = await redis_client.get_batch_id(phone)
    is_hard_max = await redis_client.should_process_buffer(phone)
    
    if current_batch != batch_id and not is_hard_max:
        logger.info("[Webhook] Newer batch exists for %s, skipping handler %s", phone, batch_id)
        return

    # Process all buffered messages
    messages = await redis_client.get_and_clear_buffer(phone)
    if messages:
        combined_message = "\n".join(messages)
        logger.info("[Webhook] Processing combined batch %s for %s (%d messages)", batch_id, phone, len(messages))
        # Call conversation engine
        await process_conversation(phone, combined_message, conversation_id, last_message_id)
    else:
        logger.info("[Webhook] No buffered messages for %s in batch %s", phone, batch_id)


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """WhatsApp Cloud API Webhook Verification."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")
    
    # Also support simple reachability test
    return {"status": "reachable", "time": datetime.now().isoformat()}


@router.post("/webhook")
async def combined_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        try:
            payload = await request.json()
        except Exception:
            logger.warning("Webhook: non-JSON body received")
            return {"status": "error", "reason": "invalid_json"}

        # Detect Meta/WhatsApp Cloud payload
        if payload.get("object") == "whatsapp_business_account":
            return await handle_whatsapp_cloud_webhook(payload, background_tasks)
            
        # Fallback to MessageBird
        return await bird_webhook(payload, background_tasks)
    except Exception as e:
        logger.critical("[Webhook] combined_webhook failure: %s", e, exc_info=True)
        return {"status": "error", "reason": str(e)}

async def handle_whatsapp_cloud_webhook(payload: dict, background_tasks: BackgroundTasks):
    """Handle inbound messages from WhatsApp Cloud API."""
    try:
        entries = payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])
                for message in messages:
                    message_id = message.get("id")
                    from_phone = message.get("from")
                    sender_phone = cloud_to_internal(from_phone)
                    
                    # Extract text content
                    message_text = ""
                    msg_type = message.get("type")
                    if msg_type == "text":
                        message_text = message.get("text", {}).get("body", "")
                    elif msg_type == "audio":
                        from app.whatsapp_cloud_client import get_media_url, _get_headers
                        media_id = message.get("audio", {}).get("id")
                        if media_id:
                            logger.info("WhatsApp Cloud: Processing audio message %s", media_id)
                            media_url = await get_media_url(media_id)
                            if media_url:
                                message_text = await process_voice_note(media_url, headers=_get_headers())
                            else:
                                logger.error("WhatsApp Cloud: Could not retrieve media URL for %s", media_id)
                        else:
                            logger.error("WhatsApp Cloud: Audio message received but no media ID found")
                        
                    if not message_text:
                        continue
                        
                    await process_inbound(sender_phone, message_text, message_id, "", background_tasks)
                    
        return {"status": "ok"}
    except Exception as e:
        logger.error("WhatsApp Cloud webhook error: %s", e)
        return {"status": "error"}

async def bird_webhook(payload: dict, background_tasks: BackgroundTasks):
    try:
        event = payload.get("event", payload.get("type", ""))
        if event and not event.endswith(".inbound"):
            return {"status": "ignored", "reason": f"event:{event}"}

        message = payload.get("payload", payload)
        message_id = message.get("id", "")
        conversation_id = message.get("conversationId", "")
        
        # Resolve sender phone
        sender_phone = None
        sender_obj = message.get("sender", {})
        contact_obj = sender_obj.get("contact", {})
        identifier = contact_obj.get("identifierValue", "")
        
        if identifier:
            sender_phone = bird_to_internal(identifier)
        
        if not sender_phone:
            logger.error("Could not resolve phone for message %s", message_id)
            return {"status": "error", "reason": "phone_resolution_failed"}

        # Extract message content
        body_obj = message.get("body", {})
        msg_type = body_obj.get("type", "text")
        message_text = ""

        if msg_type == "text":
            message_text = body_obj.get("text", {}).get("text", "")
        elif msg_type == "audio":
            audio_url = body_obj.get("audio", {}).get("url", "")
            if audio_url:
                message_text = await process_voice_note(audio_url)
        
        if not message_text:
            return {"status": "ignored", "reason": "empty_body"}

        await process_inbound(sender_phone, message_text, message_id, conversation_id, background_tasks)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Bird webhook error: %s", e)
        return {"status": "error"}


async def process_inbound(sender_phone: str, message_text: str, message_id: str, conversation_id: str, background_tasks: BackgroundTasks):
    """Entry point for all inbound messages. Handles state guards and buffering."""
    try:
        session = await redis_client.get_session(sender_phone)
        if not session:
            # Create minimal session to check state
            session = {"state": ConversationState.OPENING, "history": [], "turn_count": 0}
            
        state = session.get("state")

        # 1. CLOSED state — 24h cooldown guard
        if state == ConversationState.CLOSED:
            last_updated_str = session.get("last_updated")
            if last_updated_str:
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

        # 2. WAITING state — Low content guard
        if state == ConversationState.WAITING:
            words = message_text.strip().split()
            if len(words) < 5:
                logger.info("[Webhook] Lead %s in WAITING state, low-content ignored", sender_phone)
                return {"status": "ignored", "reason": "waiting_low_content"}
            else:
                logger.info("[Webhook] Lead %s sent substantial message, resuming from WAITING", sender_phone)
                session["state"] = ConversationState.DISCOVERY
                session["low_content_count"] = 0
                await redis_client.save_session(sender_phone, session)

        # 3. Log inbound to tracker
        try:
            from app.tracker import AlbertTracker
            tracker = AlbertTracker()
            lead = tracker.get_lead_by_phone(sender_phone)
            if not lead:
                lead = tracker.create_lead(phone=sender_phone)
            if lead:
                tracker.log_inbound(lead["id"], message_text)
        except Exception as e:
            logger.error("[Webhook] Tracker failed: %s", e)

        # 4. Check for low-content spam (WAITING transition)
        from app.conversation import check_low_content
        is_spam_threshold_reached = await check_low_content(sender_phone, message_text, session)
        if is_spam_threshold_reached:
            return {"status": "ok", "action": "entered_waiting"}

        # 5. Buffer message and start rolling timer
        batch_id = f"batch_{datetime.now().timestamp()}_{message_id}"
        await redis_client.buffer_message(sender_phone, message_text)
        await redis_client.set_batch_id(sender_phone, batch_id)
        
        background_tasks.add_task(_buffer_timeout_handler, sender_phone, batch_id, conversation_id, message_id)

        return {"status": "ok"}

    except Exception as e:
        logger.critical("[Webhook] 🚨 CRITICAL WEBHOOK FAILURE: %s", e, exc_info=True)
        return {"status": "error", "reason": str(e)}


@router.post("/admin/reset-session")
async def admin_reset_session(request: Request):
    """
    Admin endpoint to reset a lead's session (clears WAITING/CLOSED state).
    Usage: POST /admin/reset-session
    Body: {"phone": "whatsapp:+918160178327"}
    """
    try:
        body = await request.json()
        phone = body.get("phone", "").strip()
        if not phone:
            return {"status": "error", "reason": "phone is required"}
        
        # Clear the session entirely — Albert will treat them as a new lead
        await redis_client.redis.delete(f"session:{phone}")
        await redis_client.redis.delete(f"buffer:{phone}")
        await redis_client.redis.delete(f"batch_id:{phone}")
        await redis_client.redis.delete(f"calendly_sent:{phone}")
        await redis_client.redis.delete(f"low_content:{phone}")
        
        logger.info("[Admin] 🔄 Session reset for %s", phone)
        return {"status": "ok", "message": f"Session reset for {phone}. Albert will treat this as a new conversation."}
    except Exception as e:
        logger.error("[Admin] Reset failed: %s", e)
        return {"status": "error", "reason": str(e)}


@router.get("/admin/lead-status/{phone}")
async def admin_get_lead_status(phone: str):
    """
    Returns the real-time status of a lead from both Redis and Supabase.
    Usage: GET /admin/lead-status/whatsapp:+918160178327
    """
    try:
        # 1. Get Redis Session
        session = await redis_client.get_session(phone)
        
        # 2. Get Lead from Supabase
        tracker = AlbertTracker()
        lead = tracker.get_lead_by_phone(phone)
        
        if not lead:
            return {"status": "error", "message": "Lead not found in Supabase database"}
            
        # 3. Get BANT / Conversation State from Supabase
        res = supabase_client.client.table("conversation_state").select("*").eq("lead_id", lead["id"]).execute()
        conv_state = res.data[0] if res.data else {}
        
        return {
            "phone": phone,
            "lead": {
                "id": lead.get("id"),
                "name": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or "Unknown",
                "score": lead.get("signal_score", 0),
                "temperature": lead.get("temperature", "Cold"),
                "outcome": lead.get("outcome", "In Progress"),
            },
            "status": {
                "redis_state": session.get("state") if session else "NOT_IN_REDIS",
                "db_state": conv_state.get("current_state", "None"),
                "message_count": conv_state.get("message_count", 0),
                "last_active": conv_state.get("last_active_at"),
            },
            "bant_signals": {
                "budget": conv_state.get("bant_budget"),
                "authority": conv_state.get("bant_authority"),
                "need": conv_state.get("bant_need"),
                "timeline": conv_state.get("bant_timeline"),
            },
            "recent_chat_history": session.get("history", []) if session else []
        }
    except Exception as e:
        logger.error("[Admin] Lead status check failed: %s", e)
        return {"status": "error", "message": str(e)}
