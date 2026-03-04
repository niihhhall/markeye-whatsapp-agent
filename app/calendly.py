import logging
from fastapi import APIRouter, Request

from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.twilio_client import twilio_client

logger = logging.getLogger(__name__)

router = APIRouter()

CONFIRMATION_MESSAGE = (
    "Seen you've booked it in. I'll give Louis some details to prep beforehand. Speak soon."
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

    # Already in Twilio format
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
    """Handle Calendly webhook events (invitee.created)."""
    body = await request.json()

    event = body.get("event")
    logger.info("Received Calendly event: %s", event)

    if event != "invitee.created":
        logger.info("Ignoring non-invitee.created event: %s", event)
        return {"status": "ignored", "reason": "event_not_handled"}

    payload = body.get("payload", {})

    # ── Extract invitee details ──────────────────────────────────────────────
    name = payload.get("name", "")
    email = payload.get("email", "")
    scheduled_event = payload.get("scheduled_event", {})
    event_type = scheduled_event.get("name", "")
    start_time = scheduled_event.get("start_time", "")

    # ── Extract & normalise phone ────────────────────────────────────────────
    raw_phone = extract_phone_from_payload(payload)
    if not raw_phone:
        logger.warning("No phone number found in Calendly payload for %s (%s)", name, email)
        return {"status": "error", "reason": "phone_not_found"}

    phone = normalize_phone(raw_phone)
    logger.info("Calendly booking for %s | phone=%s | event=%s | start=%s", name, phone, event_type, start_time)

    booking_details = {
        "name": name,
        "email": email,
        "event_type": event_type,
        "start_time": start_time,
    }

    # ── 1. Update Supabase lead status ───────────────────────────────────────
    try:
        await supabase_client.update_lead_status(phone, "booked")
        logger.info("Supabase lead status updated to 'booked' for %s", phone)
    except Exception as exc:
        logger.error("Failed to update Supabase lead status for %s: %s", phone, exc)

    # ── 2. Update Redis session ──────────────────────────────────────────────
    try:
        session = await redis_client.get_session(phone) or {}
        session["state"] = "confirmed"
        session["booking_details"] = booking_details
        await redis_client.save_session(phone, session)
        logger.info("Redis session updated to 'confirmed' for %s", phone)
    except Exception as exc:
        logger.error("Failed to update Redis session for %s: %s", phone, exc)

    # ── 3. Send WhatsApp confirmation ────────────────────────────────────────
    try:
        twilio_client.send_message(phone, CONFIRMATION_MESSAGE)
        logger.info("WhatsApp confirmation sent to %s", phone)
    except Exception as exc:
        logger.error("Failed to send WhatsApp confirmation to %s: %s", phone, exc)

    return {
        "status": "ok",
        "phone": phone,
        "event_type": event_type,
        "start_time": start_time,
    }
