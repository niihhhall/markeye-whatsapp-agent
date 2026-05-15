import logging
import os
from typing import Optional, List
from app.config import settings

logger = logging.getLogger(__name__)


def get_provider(client_config: Optional[dict] = None) -> str:
    """
    Determine messaging provider.
    Priority: client override → global env → production guard.
    Production always forces whatsapp_cloud (Baileys = ban risk).
    """
    provider = settings.MESSAGING_PROVIDER or "whatsapp_cloud"

    if client_config and client_config.get("messaging_provider"):
        provider = client_config["messaging_provider"]

    is_production = os.getenv("ENVIRONMENT") == "production"
    if is_production and provider == "baileys":
        logger.warning(
            "⚠️ SECURITY GUARD: Baileys requested in production. "
            "Forcing whatsapp_cloud."
        )
        return "whatsapp_cloud"

    return provider


# ─── Text messages ────────────────────────────────────────────────────────────

async def send_message(
    phone: str,
    text: str,
    client_config: Optional[dict] = None,
) -> dict | None:
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_message(phone, text)
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_message(phone, text, client_id=client_id)


# ─── Typing indicator ─────────────────────────────────────────────────────────
# Cloud API: requires message_id (incoming message to reply to)
# Baileys:   message_id ignored, uses Redis pubsub outbound:typing

async def send_typing_indicator(
    phone: str,
    message_id: str = "",
    client_config: Optional[dict] = None,
) -> bool:
    """
    Show typing indicator.

    Cloud API: sends status=read + typing_indicator block.
               Requires message_id — silently skips if missing.
    Baileys:   publishes to outbound:typing channel (no message_id needed).
    """
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_typing_indicator(phone, message_id=message_id)
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_typing_indicator(phone, client_id=client_id)


# ─── Mark as read (blue ticks) ────────────────────────────────────────────────
# FIX: original signature was (phone, message_id) with no client_config.
# human_behavior.py calls mark_as_read(phone, message_id, client_config=...).

async def mark_as_read(
    phone: str,
    message_id: str,
    client_config: Optional[dict] = None,
) -> bool:
    """
    Mark incoming message as read (blue ticks on lead's screen).

    Cloud API: POST /messages with status=read + message_id
    Baileys:   publishes to outbound:mark_read channel
    """
    provider = get_provider(client_config)

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        await cloud.mark_as_read(message_id)
        return True
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.mark_as_read(phone, message_id)


# ─── Chunked messages ─────────────────────────────────────────────────────────
# NOTE: human_behavior.deliver_with_human_timing is the preferred path now.
# This function kept for backward compat (outbound.py, calcom.py still call it).

async def send_chunked_messages(
    phone: str,
    chunks: list[str],
    client_config: Optional[dict] = None,
    incoming_text: str = "",
    last_message_ts: float = 0,
    message_id: str = "",
) -> None:
    """
    Send multiple chunks.
    For inbound responses: prefer human_behavior.deliver_with_human_timing.
    For outbound (no incoming message): uses deliver_outbound_sequence.
    This shim keeps backward compat.
    """
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
        return await cloud.send_template_message(phone, final_template, language_code, components)
    else:
        logger.info(
            "[Router] Baileys requested for template %s. Skipping — awaiting raw fallback.",
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
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_media(
            phone, media_url, media_type, caption, client_id=client_id
        )


# ─── Polls ────────────────────────────────────────────────────────────────────

async def send_poll(
    phone: str,
    question: str,
    options: List[str],
    client_config: Optional[dict] = None,
) -> dict | None:
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "baileys":
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_poll(phone, question, options, client_id=client_id)
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

    if provider == "baileys":
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.forward_message(
            phone, original_msg_id, forward_to, client_id=client_id
        )
    else:
        logger.info("[Router] Forwarding not supported on Cloud API.")
        return None
