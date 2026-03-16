import asyncio
import logging
from fastapi import APIRouter, Body, BackgroundTasks
from app.models import LeadCreate
from app.supabase_client import supabase_client
from app.whatsapp_cloud_client import (
    send_message, 
    send_typing_indicator, 
    send_chunked_messages,
    send_template_message
)
from app.redis_client import redis_client
from app.models import ConversationState
from app.tracker import AlbertTracker
from app.chunker import chunk_message, calculate_typing_delay

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
        await asyncio.sleep(15)
        
        # 3. Outreach Content
        first_message_content = f"Hey {name}, Albert here|||just saw you checked out the demo for {company} and wanted to say hey|||any questions on how it all works tbh"
        
        # 4. Attempt Template Outreach (Highly Recommended for WhatsApp Cloud API)
        template_name = "after5_outreach"
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": name},
                    {"type": "text", "text": company}
                ]
            }
        ]
        
        logger.info("[Outreach] 🚀 Attempting template outreach for %s (%s)", name, sender_phone)
        template_res = await send_template_message(sender_phone, template_name, components=components)
        
        if template_res:
            logger.info("[Outreach] ✅ Template sent successfully via WhatsApp Cloud API")
            # Log the message to Supabase
            await tracker.log_outbound(lead_id, first_message_content)
        else:
            logger.warning("[Outreach] ⚠️ Template send failed. Falling back to raw text (Note: This may fail for new conversations)")
            
            # 5. Fallback: Human-like chunked text
            chunks = chunk_message(first_message_content)
            outreach_delay = calculate_typing_delay(chunks[0])
            
            await send_typing_indicator(sender_phone)
            await asyncio.sleep(outreach_delay)
            await send_chunked_messages(sender_phone, chunks)
            
            # Log to Supabase
            await tracker.log_outbound(lead_id, first_message_content)

        # 6. Initialize session with history and correct state
        target_state = ConversationState.DISCOVERY if form_data else ConversationState.OPENING
        session = {
            "state": target_state,
            "history": [{"role": "assistant", "content": first_message_content}],
            "turn_count": 1,
            "lead_data": {**(lead or {}), **(form_data or {})}
        }
        await redis_client.save_session(sender_phone, session)
        
        # 7. Update conversation state in Supabase
        state_label = "Discovery" if target_state == ConversationState.DISCOVERY else "Opening"
        await tracker.update_state(lead_id, state_label)

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
