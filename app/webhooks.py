from fastapi import APIRouter, Request, Header, HTTPException
import logging
import json
from app.config import settings
from app.redis_client import redis_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp")

@router.get("/")
async def verify_webhook(
    hub_mode: str = None, 
    hub_verify_token: str = None, 
    hub_challenge: str = None
):
    """Handshake for Meta Webhook verification."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook Verified Successfully")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")

@router.post("/")
async def handle_webhook(request: Request):
    """Handle incoming messages from WhatsApp Cloud API."""
    data = await request.json()
    
    # Process only message entries
    if "entry" not in data:
        return {"status": "ok"}

    for entry in data["entry"]:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if "messages" not in value:
                continue

            for msg in value["messages"]:
                # Extract details (Matching your Baileys payload format)
                payload = {
                    "sessionId": value.get("metadata", {}).get("phone_number_id"), 
                    "from": f"{msg['from']}@s.whatsapp.net",
                    "message": msg.get("text", {}).get("body", ""),
                    "pushName": value.get("contacts", [{}])[0].get("profile", {}).get("name", "User"),
                    "timestamp": int(msg["timestamp"]),
                    "messageId": msg["id"],
                    "provider": "whatsapp_cloud"
                }

                if payload["message"]:
                    # Publish to the same Redis channel your AI is listening to
                    # This makes the switch transparent to the rest of the app!
                    await redis_client.redis.publish("inbound", json.dumps(payload))
                    logger.info(f"[CloudWebhook] Inbound: {payload['from']} -> {payload['message'][:30]}...")

    return {"status": "ok"}
