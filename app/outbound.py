import asyncio
import logging
from fastapi import APIRouter, Body, BackgroundTasks
from app.models import LeadCreate
from app.supabase_client import supabase_client
from app.whatsapp_client import (
    send_message, 
    send_typing_indicator, 
    send_chunked_messages,
    send_template_message
)
from app.redis_client import redis_client
from app.models import ConversationState
from app.tracker import AlbertTracker
from app.chunker import chunk_message, calculate_typing_delay, format_message
from app.templates import OUTREACH_TEMPLATES, FOLLOW_UP_TEMPLATE
import random

logger = logging.getLogger(__name__)
router = APIRouter()

async def send_initial_outreach(name: str, phone_raw: str, company: str, form_data: dict = None):
    """Sends the first outbound message after a delay."""
    try:
        tracker = AlbertTracker()
        
        # Normalize phone to internal format: whatsapp:+[digits]
        digits = "".join(filter(str.isdigit, str(phone_raw)))
        sender_phone = f"whatsapp:+{digits}"

        # 1. Save to Supabase via Tracker (or get existing)
        lead = await tracker.get_lead_by_phone(sender_phone)
        if not lead:
            lead = await tracker.create_lead(
                phone=sender_phone, 
                first_name=name, 
                company=company, 
                lead_source=form_data.get("source", "Website Demo Form") if form_data else "Website Demo Form",
                form_message=form_data.get("message", "") if form_data else ""
            )
        
        lead_id = lead.get("id") if lead else "unknown"

        # 2. Start outreach sequence (Wait for lead to settle)
        # Simulation Reliability: Skip delay for testing
        is_sim = form_data and form_data.get("source") == "Interactive Reset Simulation"
        if not is_sim:
            await asyncio.sleep(15)
        
        # 3. Outreach Content
        raw_template = random.choice(OUTREACH_TEMPLATES)
        first_message_content = raw_template.format(name=name, company_name=company)
        
        # 4. Attempt Template Outreach (Highly Recommended for WhatsApp Cloud API)
        template_name = "after5_outreach_2"
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": name}
                ]
            }
        ]
        
        if is_sim:
            template_res = None
            logger.info("[Outreach] 🧪 Simulation detected: skipped delay and skipping template.")
        else:
            logger.info("[Outreach] 🚀 Attempting template outreach for %s (%s)", name, sender_phone)
            template_res = await send_template_message(sender_phone, template_name, components=components)
        
        # 6. Initialize or Update session with history and correct state
        target_state = ConversationState.DISCOVERY if form_data else ConversationState.OPENING
        
        # Check if a session already exists (e.g. user texted while we were processing)
        session = await redis_client.get_session(sender_phone)
        if not session:
            session = {
                "state": target_state,
                "history": [],
                "turn_count": 0,
                "lead_data": {**(lead or {}), **(form_data or {})}
            }
        
        # Merge history: Add outreach message EARLY to prevent race conditions
        session["history"].append({"role": "assistant", "content": first_message_content})
        session["turn_count"] = session.get("turn_count", 0) + 1
        session["state"] = target_state
        
        await redis_client.save_session(sender_phone, session)
        
        # 7. Update conversation state in Supabase
        state_label = "Discovery" if target_state == ConversationState.DISCOVERY else "Opening"
        await tracker.update_state(lead_id, state_label)

        # 8. Delivery (now happening after session is safe)
        if template_res:
            logger.info("[Outreach] ✅ Template sent successfully via WhatsApp Cloud API")
            # Log to Supabase (already logged to session history)
            await tracker.log_outbound(lead_id, first_message_content)
        else:
            logger.warning("[Outreach] ⚠️ Template send failed. Falling back to raw text.")
            
            # 5. Fallback: Human-like delivery — bypass chunking for template
            chunks = chunk_message(first_message_content, is_template=True)
            
            # Note: typing indicator and delays are handled INSIDE send_chunked_messages.
            # This is where the long delays (>30s) happen.
            await send_chunked_messages(sender_phone, chunks)
            
            # Log to Supabase
            await tracker.log_outbound(lead_id, first_message_content)

    except Exception as e:
        logger.error("[Outreach] 🚨 Failed to send initial outreach for %s: %s", phone_raw, e, exc_info=True)

@router.post("/send-outbound")
async def send_outbound(lead: LeadCreate, background_tasks: BackgroundTasks = None):
    asyncio.create_task(send_initial_outreach(lead.name, lead.phone, lead.company))
    return {"status": "outreach_scheduled"}

@router.post("/form-webhook")
async def form_webhook(payload: dict):
    """Endpoint for n8n/website form submissions."""
    name = payload.get("first_name") or payload.get("name")
    phone = payload.get("phone")
    company = payload.get("company", "your business")
    
    if not name or not phone:
        return {"error": "name and phone required"}
        
    asyncio.create_task(send_initial_outreach(name, phone, company, payload))
    return {"status": "outreach_scheduled"}

async def send_follow_up_message(lead_id: str, name: str, phone: str):
    """Sends the 24-hour follow-up message."""
    try:
        tracker = AlbertTracker()
        
        # 1. Formatting the follow-up content
        follow_up_content = FOLLOW_UP_TEMPLATE.format(name=name)
        
        logger.info("[Follow-up] 🚀 Sending follow-up to %s (%s)", name, phone)
        
        # 2. Human-like delivery — bypass chunking for follow-up template
        from app.chunker import chunk_message
        chunks = chunk_message(follow_up_content, is_template=True)
        await send_chunked_messages(phone, chunks)
        
        # 3. Log to Supabase
        await tracker.log_outbound(lead_id, follow_up_content)
        
        # 4. Initialize or Update session state
        session = await redis_client.get_session(phone)
        if session:
            session["history"].append({"role": "assistant", "content": follow_up_content})
            session["turn_count"] = session.get("turn_count", 1) + 1
            await redis_client.save_session(phone, session)
        
        logger.info("[Follow-up] ✅ Follow-up sent to %s", name)

    except Exception as e:
        logger.error("[Follow-up] 🚨 Failed to send follow-up for %s: %s", name, e, exc_info=True)

@router.post("/follow-up")
async def trigger_follow_up(payload: dict = Body(...)):
    """Admin endpoint to manually trigger a follow-up for a lead."""
    lead_id = payload.get("lead_id")
    name = payload.get("name")
    phone = payload.get("phone")
    
    if not all([lead_id, name, phone]):
        return {"error": "lead_id, name, and phone are required"}
        
    asyncio.create_task(send_follow_up_message(lead_id, name, phone))
    return {"status": "follow_up_scheduled"}
