import logging
import os
from typing import Optional, List
from app.config import settings

logger = logging.getLogger(__name__)

def get_provider(client_config: Optional[dict] = None) -> str:
    """
    Determine which messaging provider to use.
    Priority:
    1. Per-client config ('messaging_provider' in 'clients' table)
    2. Global environment variable (MESSAGING_PROVIDER)
    3. Production Guard: Always force 'whatsapp_cloud' in production.
    """
    # 1. Check Global Env
    provider = settings.MESSAGING_PROVIDER or "whatsapp_cloud"
    
    # 2. Check Client Override
    if client_config and client_config.get("messaging_provider"):
        provider = client_config["messaging_provider"]
    
    # 3. Production Guard (CRITICAL)
    # Never allow Baileys in production to avoid number bans.
    is_production = os.getenv("ENVIRONMENT") == "production"
    if is_production and provider == "baileys":
        logger.warning("⚠️ SECURITY GUARD: Baileys requested in production. Forcing fallback to 'whatsapp_cloud'.")
        return "whatsapp_cloud"
        
    return provider

async def send_message(phone: str, text: str, client_config: Optional[dict] = None) -> dict | None:
    """Unified routing for single text messages."""
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None
    
    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_message(phone, text)
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_message(phone, text, client_id=client_id)

async def send_chunked_messages(
    phone: str, 
    chunks: list[str], 
    client_config: Optional[dict] = None,
    # Optional params for cloud API human-like sequence
    incoming_text: str = "", 
    last_message_ts: float = 0, 
    message_id: str = ""
) -> None:
    """Unified routing for chunked/delayed message sequences."""
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        await cloud.send_chunked_messages(phone, chunks, incoming_text, last_message_ts, message_id)
    else:
        from app.baileys_bridge import baileys_bridge
        await baileys_bridge.send_chunked_messages(phone, chunks, client_id=client_id)

async def send_typing_indicator(phone: str, message_id: str = "", client_config: Optional[dict] = None) -> bool:
    """Unified routing for typing status."""
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_typing_indicator(phone, message_id)
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_typing_indicator(phone, client_id=client_id)

async def mark_as_read(phone: str, message_id: str, client_config: Optional[dict] = None) -> bool:
    """Unified routing for marking messages as read."""
    provider = get_provider(client_config)
    
    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        await cloud.mark_as_read(message_id)
        return True
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.mark_as_read(phone, message_id)

async def send_template_message(
    phone: str, 
    client_config: Optional[dict] = None,
    template_name: Optional[str] = None,
    language_code: str = "en_GB", 
    components: Optional[list] = None
) -> dict | None:
    """
    Template routing. 
    Meta official API uses templates. Baileys uses raw text fallback.
    """
    provider = get_provider(client_config)
    
    # Use client-specific template if available, fallback to provided or default
    final_template = template_name or (client_config.get("outreach_template_name") if client_config else "markeye_outreach")

    if provider == "whatsapp_cloud":
        from app import whatsapp_client as cloud
        return await cloud.send_template_message(phone, final_template, language_code, components)
    else:
        # Baileys doesn't support official templates.
        # Calling code must provide a text fallback or we skip.
        logger.info(f"[Router] Baileys requested for template {final_template}. Skiping template, awaiting raw fallback.")
        return None

async def send_media(
    phone: str, 
    media_url: str, 
    media_type: str = "document", 
    caption: str = "", 
    client_config: Optional[dict] = None
) -> dict | None:
    """Unified routing for media messages."""
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "whatsapp_cloud":
        # Cloud API media not yet fully implemented in whatsapp_client.py 
        # (would need upload/id logic). For now, return None or implement if needed.
        logger.warning("[Router] Media requested for whatsapp_cloud. Not fully supported yet.")
        return None
    else:
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_media(phone, media_url, media_type, caption, client_id=client_id)

async def send_poll(phone: str, question: str, options: List[str], client_config: Optional[dict] = None) -> dict | None:
    """Unified routing for interactive polls (Baileys only for now)."""
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "baileys":
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.send_poll(phone, question, options, client_id=client_id)
    else:
        logger.info("[Router] Polls not supported on official WhatsApp Cloud API yet. Skipping.")
        return None

async def forward_message(phone: str, original_msg_id: str, forward_to: str, client_config: Optional[dict] = None) -> dict | None:
    """Unified routing for forwarding messages (Escalation)."""
    provider = get_provider(client_config)
    client_id = client_config.get("id") if client_config else None

    if provider == "baileys":
        from app.baileys_bridge import baileys_bridge
        return await baileys_bridge.forward_message(phone, original_msg_id, forward_to, client_id=client_id)
    else:
        # Official API doesn't have a direct 'forward' via Graph API 
        # normally we'd send a new message or use a specialized endpoint if exists.
        logger.info("[Router] Forwarding not supported on official API. Emulating with tracker log.")
        return None
