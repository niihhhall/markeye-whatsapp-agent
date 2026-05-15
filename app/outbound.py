import asyncio
import logging
from fastapi import APIRouter, Body, BackgroundTasks, Depends, HTTPException, Header
from app.config import settings
from app.models import LeadCreate
from app.supabase_client import supabase_client
from app.message_router import (
    send_message, 
    send_typing_indicator, 
    send_chunked_messages,
    send_template_message
)
from app.redis_client import redis_client
from app.models import ConversationState
from app.tracker import MarkTracker
from app.chunker import chunk_message, calculate_typing_delay, format_message
from app.templates import OUTREACH_TEMPLATES, FOLLOW_UP_TEMPLATE
from app.phone_utils import normalize_phone
from app.name_utils import clean_personal_name, clean_company_name
from app.client_manager import client_manager
import random

logger = logging.getLogger(__name__)
router = APIRouter()

async def verify_outbound_api_key(x_api_key: str = Header(default="")):
    """Guard outbound endpoints with a static API key."""
    if settings.OUTBOUND_API_KEY and x_api_key != settings.OUTBOUND_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

async def check_outbound_rate_limit(phone: str, max_per_hour: int = 10) -> bool:
    """Returns True if message is allowed, False if rate limit exceeded."""
    rate_key = f"outbound_rate:{phone}"
    count = await redis_client.redis.incr(rate_key)
    if count == 1:
        await redis_client.redis.expire(rate_key, 3600)  # 1 hour window
    if count > max_per_hour:
        logger.warning("[Outbound] Rate limit exceeded for %s (%d msgs/hr)", phone, count)
        return False
    return True

async def send_initial_outreach(name_raw: str, phone_raw: str, company_raw: str, form_data: dict = None, client_id: str = None):
    """Sends the first outbound message after a delay."""
    try:
        tracker = MarkTracker()
        
        # Resolve Client Config
        client_config = None
        if client_id:
            client_config = await client_manager.get_client_by_id(client_id)
        
        # Normalize name and company for display and storage
        name = clean_personal_name(name_raw)
        company = clean_company_name(company_raw)
        
        # Normalize phone to internal format: whatsapp:+[digits]
        sender_phone = normalize_phone(phone_raw)

        # 1. Save to Supabase via Tracker (or get existing)
        lead = await tracker.get_lead_by_phone(sender_phone)
        if not lead:
            lead = await tracker.create_lead(
                phone=sender_phone, 
                first_name=name, 
                company=company, 
                lead_source=form_data.get("source", "Website Demo Form") if form_data else "Website Demo Form",
                form_message=form_data.get("message", "") if form_data else "",
                client_id=client_id
            )
        
        lead_id = lead.get("id") if lead else "unknown"

        # 2. Start outreach sequence (Wait for lead to settle)
        # Simulation Reliability: Skip delay for testing
        is_sim = form_data and form_data.get("source") == "Interactive Reset Simulation"
        # 0. Rate Limit Check (Enhancement: TASK 8)
        if not await check_outbound_rate_limit(sender_phone):
            return

        # 3. Outreach Content
        if client_config and client_config.get("greeting_message"):
            business_name = client_config.get("business_name", "Markeye")
            greeting_tpl = client_config.get("greeting_message")
            first_message_content = greeting_tpl.format(business_name=business_name, name=name)
        else:
            raw_template = random.choice(OUTREACH_TEMPLATES)
            first_message_content = raw_template.format(name=name, company_name=company)
        
        # 4. Attempt Template Outreach (Highly Recommended for WhatsApp Cloud API)
        template_name = "markeye_outreach"
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": name},
                    {"type": "text", "text": company}
                ]
            }
        ]
        
        if is_sim:
            logger.info("[Outreach] 🧪 Simulation: Skipping template, using raw text fallback.")
            template_res = None
        else:
            logger.info("[Outreach] 🚀 Attempting template outreach for %s (%s)", name, sender_phone)
            template_res = await send_template_message(
                sender_phone, 
                client_config=client_config,
                template_name=template_name, 
                language_code="en_GB", 
                components=components
            )
        
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
            await tracker.log_outbound(lead_id, first_message_content, client_id=client_id)
        else:
            if not is_sim:
                logger.warning("[Outreach] ⚠️ Template send failed or Baileys selected. Falling back to raw text.")
            
            # Note: typing indicator and delays are handled INSIDE deliver_outbound_sequence.
            from app.human_behavior import deliver_outbound_sequence
            chunks = chunk_message(first_message_content, is_template=True)
            await deliver_outbound_sequence(sender_phone, chunks, client_config=client_config)
            
            # Log to Supabase
            await tracker.log_outbound(lead_id, first_message_content, client_id=client_id)

    except Exception as e:
        logger.error("[Outreach] 🚨 Failed to send initial outreach for %s: %s", phone_raw, e, exc_info=True)

@router.post("/send-outbound", dependencies=[Depends(verify_outbound_api_key)])
async def send_outbound(lead: LeadCreate, background_tasks: BackgroundTasks = None):
    asyncio.create_task(send_initial_outreach(lead.name, lead.phone, lead.company, client_id=lead.client_id))
    return {"status": "outreach_scheduled"}

@router.post("/form-webhook", dependencies=[Depends(verify_outbound_api_key)])
async def form_webhook(payload: dict):
    """Endpoint for n8n/website form submissions."""
    name = payload.get("first_name") or payload.get("name")
    phone = payload.get("phone")
    company = payload.get("company", "your business")
    client_id = payload.get("client_id")
    
    if not name or not phone:
        return {"error": "name and phone required"}
        
    asyncio.create_task(send_initial_outreach(name, phone, company, payload, client_id=client_id))
    return {"status": "outreach_scheduled"}

async def send_follow_up_message(lead_id: str, name: str, phone: str):
    """Sends the 24-hour follow-up message."""
    try:
        # Rate Limit Check (Enhancement: TASK 8)
        if not await check_outbound_rate_limit(phone):
            return

        tracker = MarkTracker()
        
        # 1. Formatting the follow-up content
        follow_up_content = FOLLOW_UP_TEMPLATE.format(name=name)
        
        logger.info("[Follow-up] 🚀 Sending follow-up to %s (%s)", name, phone)
        
        # 2. Human-like delivery — bypass chunking for follow-up template
        from app.chunker import chunk_message
        from app.human_behavior import deliver_outbound_sequence
        chunks = chunk_message(follow_up_content, is_template=True)
        await deliver_outbound_sequence(phone, chunks)
        
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

@router.post("/follow-up", dependencies=[Depends(verify_outbound_api_key)])
async def trigger_follow_up(payload: dict = Body(...)):
    """Admin endpoint to manually trigger a follow-up for a lead."""
    lead_id = payload.get("lead_id")
    name = payload.get("name")
    phone = payload.get("phone")
    
    if not all([lead_id, name, phone]):
        return {"error": "lead_id, name, and phone are required"}
        
    asyncio.create_task(send_follow_up_message(lead_id, name, phone))
    return {"status": "follow_up_scheduled"}
