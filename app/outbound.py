import asyncio
from fastapi import APIRouter, Body, BackgroundTasks
from app.models import LeadCreate
from app.supabase_client import supabase_client
from app.twilio_client import twilio_client
from app.redis_client import redis_client
from app.models import ConversationState

router = APIRouter()

async def send_initial_outreach(name: str, phone: str, company: str):
    """Sends the first outbound message after a delay."""
    # 1. Save to Supabase
    await supabase_client.create_lead(name, phone, company)
    
    # 2. Start outreach sequence (delay for realism if needed, or follow instructions)
    # instructions say wait 30 seconds
    await asyncio.sleep(30)
    
    # 3. Initialize session
    session = {
        "state": ConversationState.OPENING,
        "history": [],
        "turn_count": 0,
        "lead_data": {"name": name, "phone": phone, "company": company}
    }
    await redis_client.save_session(phone, session)
    
    # 4. Send first message
    first_message = f"Hey {name}, this is Albert from After5 Digital. I saw your inquiry about {company} — wanted to reach out and see how we can help!"
    twilio_client.send_message(phone, first_message)
    
    # 5. Log and update
    await supabase_client.update_lead_status(phone, "outreach_sent")
    await supabase_client.log_message(phone, "outbound", first_message, ConversationState.OPENING)
    
    # Initial history
    await redis_client.add_to_history(phone, "assistant", first_message)

@router.post("/send-outbound")
async def send_outbound(lead: LeadCreate, background_tasks: BackgroundTasks = None):
    # If BackgroundTasks not provided directly (e.g. from FastAPI route), 
    # we can use a local one or just run it. 
    # But usually it's passed.
    asyncio.create_task(send_initial_outreach(lead.name, lead.phone, lead.company))
    return {"status": "outreach_scheduled"}

@router.post("/form-webhook")
async def form_webhook(payload: dict):
    """Endpoint for n8n/website form submissions."""
    name = payload.get("name")
    phone = payload.get("phone")
    company = payload.get("company")
    
    if not name or not phone:
        return {"error": "name and phone required"}
        
    asyncio.create_task(send_initial_outreach(name, phone, company))
    return {"status": "outreach_scheduled"}
