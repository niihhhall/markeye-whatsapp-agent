import logging
from fastapi import APIRouter, Request

from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.messaging import send_chunked_messages

logger = logging.getLogger(__name__)

router = APIRouter()

CONFIRMATION_MESSAGE = (
    "Seen you've booked it in. I'll give the team some details to prep beforehand. Speak soon."
)


def normalize_phone(raw: str) -> str:
    """
    Normalise a phone number to the whatsapp:+XXXXXXXXXXX format.

    Handles:
    - +447700900000       → whatsapp:+447700900000
    - 07700900000         → whatsapp:+447700900000  (UK local, strip 0, prepend +44)
    - 447700900000        → whatsapp:+447700900000  (no + prefix)
    - whatsapp:+447700900000 → whatsapp:+447700900000  (already correct)
    """
    phone = raw.strip()

    # Already in internal whatsapp: format
    if phone.startswith("whatsapp:"):
        return phone

    # Strip any spaces or dashes
    phone = phone.replace(" ", "").replace("-", "")

    if phone.startswith("+"):
        # Full international format: +447700900000
        return f"whatsapp:{phone}"

    if phone.startswith("0"):
        # UK local format: 07700900000 → +447700900000
        return f"whatsapp:+44{phone[1:]}"

    # Assume digits only with country code but no +: 447700900000
    return f"whatsapp:+{phone}"


def extract_phone_from_payload(payload: dict) -> str | None:
    """
    Extract the WhatsApp phone number from the Calendly payload.

    Priority:
    1. questions_and_answers – match on 'phone', 'whatsapp', or 'mobile' (case-insensitive)
    2. tracking.utm_content / tracking.utm_source as fallback
    """
    questions = payload.get("questions_and_answers", [])
    for qa in questions:
        question = qa.get("question", "").lower()
        if any(kw in question for kw in ("phone", "whatsapp", "mobile")):
            answer = qa.get("answer", "").strip()
            if answer:
                logger.debug("Found phone in questions_and_answers: %s", answer)
                return answer

    # Fallback: UTM params
    tracking = payload.get("tracking", {})
    for field in ("utm_content", "utm_source"):
        value = tracking.get(field, "").strip()
        if value and (value.startswith("+") or value.startswith("0") or value.isdigit()):
            logger.debug("Found phone in tracking.%s: %s", field, value)
            return value

    return None


@router.post("/calendly-webhook")
async def calendly_webhook(request: Request):
    """Handle Calendly webhook events."""
    body = await request.json()
    event = body.get("event")
    payload = body.get("payload", {})
    
    from app.tracker import MarkTracker
    tracker = MarkTracker()

    # Extract & normalise phone
    raw_phone = extract_phone_from_payload(payload)
    if not raw_phone:
        return {"status": "error", "reason": "phone_not_found"}
    phone = normalize_phone(raw_phone)
    
    lead = await tracker.get_lead_by_phone(phone)
    event_uri = payload.get("event") # Calendly often uses event URI as ID

    if event == "invitee.created":
        scheduled_at = payload.get("scheduled_event", {}).get("start_time")
        if lead:
            await tracker.confirm_booking(
                lead_id=lead["id"],
                calendly_event_id=event_uri,
                scheduled_at=scheduled_at
            )
            # Clear Redis session to force state refresh on next user message
            await redis_client.redis.delete(f"session:{phone}")
            logger.info("[Calendly] 🧹 Cleared Redis session for %s after booking confirmation", phone)
        
        # ── 3. Send WhatsApp confirmation ──
        try:
            chunks = [
                "Seen you've booked it in",
                "I'll give the team some details to prep beforehand. Speak soon."
            ]
            await send_chunked_messages(phone, chunks)
        except Exception as exc:
            logger.error("Failed to send WhatsApp confirmation to %s: %s", phone, exc)

    elif event == "invitee.canceled":
        if lead:
            await tracker.cancel_booking(
                lead_id=lead["id"],
                calendly_event_id=event_uri
            )

    return {"status": "ok"}
