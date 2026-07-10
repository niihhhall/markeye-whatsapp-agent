import logging
import os
import re
import httpx
from typing import Optional, List
from app.config import settings

logger = logging.getLogger(__name__)

# Deterministic dash removal — Mark must NEVER send em/en dashes (bot tell).
# The prompt bans them, but LLMs slip, so we strip them from every outgoing
# message. Single hyphens inside words/URLs (e.g. "free-discovery-call") are
# left intact — only dash-as-punctuation forms are replaced with a comma.
_DASH_SUBS = [
    (re.compile(r"\s*—\s*"), ", "),      # em dash
    (re.compile(r"\s*–\s*"), ", "),      # en dash
    (re.compile(r"\s*--+\s*"), ", "),    # double (or more) hyphen used as a dash
    (re.compile(r"\s+-\s+"), ", "),      # spaced hyphen used as a dash
]


def _sanitize_dashes(text: str) -> str:
    """Replace dash-as-punctuation with commas. Leaves hyphenated words/URLs alone."""
    if not text:
        return text
    for pattern, repl in _DASH_SUBS:
        text = pattern.sub(repl, text)
    return text


def get_provider(client_config: Optional[dict] = None) -> str:
    """
    Determine messaging provider.
    Priority: client override → global env → production guard.
    Production always forces whatsapp_cloud (Baileys = ban risk).
    """
    provider = settings.MESSAGING_PROVIDER or "whatsapp_cloud"

    if client_config and client_config.get("messaging_provider"):
        provider = client_config["messaging_provider"]

    # "baileys" routes to the in-process Baileys bridge (Redis pub/sub) via the
    # else-branch of each send_* function. Do NOT remap to openwa — the OpenWA
    # gateway is not part of this deployment.

    is_production = os.getenv("ENVIRONMENT") == "production"
    # Keep guard active only for legacy baileys if it somehow slips through
    if is_production and provider == "legacy_baileys":
        logger.warning(
            "⚠️ SECURITY GUARD: Legacy Baileys requested in production. "
            "Forcing whatsapp_cloud."
        )
        return "whatsapp_cloud"

    return provider


async def _call_openwa_api(endpoint: str, payload: dict, method: str = "POST") -> dict | None:
    """Helper to perform requests on the self-hosted OpenWA API gateway."""
    url = f"{settings.OPENWA_API_URL.rstrip('/')}/api{endpoint}"
    headers = {
        "X-API-Key": settings.OPENWA_API_KEY,
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method.upper() == "POST":
                resp = await client.post(url, headers=headers, json=payload)
            else:
                resp = await client.get(url, headers=headers)
                
            if resp.status_code in [200, 201]:
                return resp.json()
            else:
                logger.error(f"[OpenWA API] Call failed to {endpoint}: {resp.status_code} — {resp.text}")
                return None
    except Exception as e:
        logger.error(f"[OpenWA API] Connection error: {e}")
        return None


# ─── Text messages ────────────────────────────────────────────────────────────

async def send_message(
    phone: str,
    text: str,
    client_config: Optional[dict] = None,
) -> dict | None:
    text = _sanitize_dashes(text)
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_message(phone, text, client_config=client_config)
    elif provider == "openwa":
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        to_jid = f"{phone_id}@c.us" if "@" not in phone_id else phone_id
        payload = {
            "sessionId": client_id,
            "to": to_jid,
            "text": text
        }
        res = await _call_openwa_api("/messages/send-text", payload)
        if res:
            return {"status": "sent", "provider": "openwa", "messageId": res.get("id")}
        return None
    else:
        try:
            from app.baileys_bridge import baileys_bridge
            return await baileys_bridge.send_message(phone, text, client_id=client_id)
        except ImportError:
            logger.error("Baileys bridge not available and provider is not OpenWA or Cloud API")
            return None


# ─── Typing indicator ─────────────────────────────────────────────────────────

async def send_typing_indicator(
    phone: str,
    message_id: str = "",
    client_config: Optional[dict] = None,
) -> bool:
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_typing_indicator(phone, message_id=message_id, client_config=client_config)
    elif provider == "openwa":
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        to_jid = f"{phone_id}@c.us" if "@" not in phone_id else phone_id
        payload = {
            "sessionId": client_id,
            "to": to_jid,
            "presence": "composing"
        }
        await _call_openwa_api("/messages/send-presence-update", payload)
        return True
    else:
        try:
            from app.baileys_bridge import baileys_bridge
            return await baileys_bridge.send_typing_indicator(phone, client_id=client_id)
        except ImportError:
            return False


# ─── Mark as read (blue ticks) ────────────────────────────────────────────────

async def mark_as_read(
    phone: str,
    message_id: str,
    client_config: Optional[dict] = None,
) -> bool:
    provider = get_provider(client_config)

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        await cloud.mark_as_read(message_id, client_config=client_config)
        return True
    elif provider == "openwa":
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        to_jid = f"{phone_id}@c.us" if "@" not in phone_id else phone_id
        payload = {
            "sessionId": client_config.get("id") if client_config else None,
            "to": to_jid,
            "messageId": message_id
        }
        await _call_openwa_api("/messages/mark-read", payload)
        return True
    else:
        try:
            from app.baileys_bridge import baileys_bridge
            return await baileys_bridge.mark_as_read(phone, message_id)
        except ImportError:
            return False


# ─── Chunked messages ─────────────────────────────────────────────────────────

async def send_chunked_messages(
    phone: str,
    chunks: list[str],
    client_config: Optional[dict] = None,
    incoming_text: str = "",
    last_message_ts: float = 0,
    message_id: str = "",
) -> None:
    from app.human_behavior import deliver_outbound_sequence
    await deliver_outbound_sequence(phone, chunks, client_config=client_config)


# ─── Template messages ────────────────────────────────────────────────────────

async def send_template_message(
    phone: str,
    client_config: Optional[dict] = None,
    template_name: Optional[str] = None,
    language_code: str = "en_GB",
    components: Optional[list] = None,
) -> dict | None:
    provider = get_provider(client_config)
    final_template = template_name or (
        client_config.get("outreach_template_name") if client_config else "markeye_outreach"
    )

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_template_message(phone, final_template, language_code, components, client_config=client_config)
    else:
        logger.info(
            "[Router] Provider %s requested for template %s. Skipping — awaiting raw fallback.",
            provider,
            final_template,
        )
        return None


# ─── Media ────────────────────────────────────────────────────────────────────

async def send_media(
    phone: str,
    media_url: str,
    media_type: str = "document",
    caption: str = "",
    client_config: Optional[dict] = None,
) -> dict | None:
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        logger.warning("[Router] Media via whatsapp_cloud not fully implemented.")
        return None
    elif provider == "openwa":
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        to_jid = f"{phone_id}@c.us" if "@" not in phone_id else phone_id
        payload = {
            "sessionId": client_id,
            "to": to_jid,
            "url": media_url,
            "type": media_type,
            "caption": caption
        }
        await _call_openwa_api("/messages/send-media", payload)
        return {"status": "sent", "provider": "openwa"}
    else:
        try:
            from app.baileys_bridge import baileys_bridge
            return await baileys_bridge.send_media(
                phone, media_url, media_type, caption, client_id=client_id
            )
        except ImportError:
            return None


# ─── Polls ────────────────────────────────────────────────────────────────────

async def send_poll(
    phone: str,
    question: str,
    options: List[str],
    client_config: Optional[dict] = None,
) -> dict | None:
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "openwa":
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        to_jid = f"{phone_id}@c.us" if "@" not in phone_id else phone_id
        payload = {
            "sessionId": client_id,
            "to": to_jid,
            "name": question,
            "options": options
        }
        await _call_openwa_api("/messages/send-poll", payload)
        return {"status": "sent", "provider": "openwa"}
    elif provider == "baileys":
        try:
            from app.baileys_bridge import baileys_bridge
            return await baileys_bridge.send_poll(phone, question, options, client_id=client_id)
        except ImportError:
            return None
    else:
        logger.info("[Router] Polls not supported on Cloud API. Skipping.")
        return None


# ─── Forward (escalation) ─────────────────────────────────────────────────────

async def forward_message(
    phone: str,
    original_msg_id: str,
    forward_to: str,
    client_config: Optional[dict] = None,
) -> dict | None:
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "openwa":
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        target_id = forward_to.split(':')[-1] if ':' in forward_to else forward_to
        target_jid = f"{target_id}@c.us" if "@" not in target_id else target_id
        
        payload = {
            "sessionId": client_id,
            "to": target_jid,
            "messageId": original_msg_id
        }
        await _call_openwa_api("/messages/forward", payload)
        return {"status": "forwarded", "provider": "openwa"}
    elif provider == "baileys":
        try:
            from app.baileys_bridge import baileys_bridge
            return await baileys_bridge.forward_message(
                phone, original_msg_id, forward_to, client_id=client_id
            )
        except ImportError:
            return None
    else:
        logger.info("[Router] Forwarding not supported on Cloud API.")
        return None
