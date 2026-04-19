import logging
import json
from fastapi import APIRouter, Request, HTTPException

from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.message_router import send_chunked_messages
from app.tracker import MarkTracker
from app.phone_utils import normalize_phone

logger = logging.getLogger(__name__)
router = APIRouter()

def extract_phone_from_calcom(payload: dict) -> str | None:
    """
    Extract the phone number from Cal.com payload.
    Checks attendees and responses.
    """
    # 1. Check attendees
    attendees = payload.get("attendees", [])
    for attendee in attendees:
        phone = attendee.get("phoneNumber")
        if phone:
            return phone

    # 2. Check responses (custom form fields)
    responses = payload.get("responses", {})
    # Look for common key names
    for key in ["phone", "phoneNumber", "whatsapp", "mobile"]:
        val = responses.get(key)
        if val:
            return str(val)
            
    return None

@router.post("/calcom-webhook")
async def calcom_webhook(request: Request):
    """Handle Cal.com webhook events."""
    try:
        body = await request.json()
        logger.info(f"[Cal.com Webhook] Received event: {body.get('triggerEvent')}")
    except Exception as e:
        logger.error(f"Failed to parse Cal.com webhook: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    trigger_event = body.get("triggerEvent")
    payload = body.get("payload", {})
    
    tracker = MarkTracker()

    # Extract & normalise phone
    raw_phone = extract_phone_from_calcom(payload)
    if not raw_phone:
        logger.warning("[Cal.com] No phone number found in payload")
        return {"status": "error", "reason": "phone_not_found"}

    phone = normalize_phone(raw_phone)
    lead = await tracker.get_lead_by_phone(phone)
    
    booking_id = str(payload.get("uid") or payload.get("id"))
    
    if trigger_event == "BOOKING_CREATED":
        start_time = payload.get("startTime")
        if lead:
            await tracker.confirm_booking(
                lead_id=lead["id"],
                calendly_event_id=booking_id, # Re-using column for external dashboard compatibility
                scheduled_at=start_time
            )
            # Clear Redis session
            await redis_client.redis.delete(f"session:{phone}")
            logger.info(f"[Cal.com] 🧹 Cleared session for {phone} after booking")
        
        # Send WhatsApp confirmation
        try:
            chunks = [
                "Seen you've booked it in (via Cal.com)",
                "I'll give the team some details to prep beforehand. Speak soon."
            ]
            await send_chunked_messages(phone, chunks)
        except Exception as exc:
            logger.error(f"Failed to send confirmation to {phone}: {exc}")

    elif trigger_event == "BOOKING_CANCELLED":
        if lead:
            await tracker.cancel_booking(
                lead_id=lead["id"],
                calendly_event_id=booking_id
            )
            logger.info(f"[Cal.com] ❌ Booking cancelled for {phone}")

    return {"status": "ok"}
