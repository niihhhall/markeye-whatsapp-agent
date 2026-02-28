import asyncio
from fastapi import APIRouter, Request, BackgroundTasks
from app.redis_client import redis_client
from app.conversation import process_conversation
from app.config import settings

router = APIRouter()

async def buffer_timeout_handler(phone: str):
    """Waits for buffer timer to expire, then processes combined message."""
    await asyncio.sleep(settings.INPUT_BUFFER_SECONDS)
    
    # Check if timer is still active (hasn't been reset)
    if await redis_client.is_timer_active(phone):
        # Still active means another message might have reset it? 
        # Actually our implementation of set_buffer_timer just overwrites.
        # So we wait and then check if it's expired or if we are the "last" one.
        pass
    
    # Wait until the timer key actually expires
    while await redis_client.is_timer_active(phone):
        await asyncio.sleep(0.5)

    # Process buffered messages
    messages = await redis_client.get_and_clear_buffer(phone)
    if messages:
        combined_message = " ".join(messages)
        await process_conversation(phone, combined_message)

@router.post("/webhook")
async def twilio_webhook(request: Request, background_tasks: BackgroundTasks):
    """Twilio WhatsApp webhook handler."""
    form_data = await request.form()
    
    phone = form_data.get("From")
    body = form_data.get("Body")
    message_sid = form_data.get("MessageSid")

    if not phone or not body:
        return {"status": "ignored", "reason": "missing_data"}

    # 1. Dedup
    if await redis_client.check_dedup(message_sid):
        return {"status": "ignored", "reason": "duplicate"}

    # 2. Buffer message
    await redis_client.buffer_message(phone, body)
    
    # 3. Set/Reset buffer timer
    await redis_client.set_buffer_timer(phone)
    
    # 4. Schedule processing if not already scheduled
    # Simple way: just always fire a background task that waits and checks
    background_tasks.add_task(buffer_timeout_handler, phone)

    return {"status": "ok"}
