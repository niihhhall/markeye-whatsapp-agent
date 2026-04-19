import logging
from typing import Optional, List
from app.message_router import (
    send_message as router_send,
    send_chunked_messages as router_chunked,
    send_typing_indicator as router_typing,
    mark_as_read as router_read,
    send_media as router_media,
    send_poll as router_poll,
    forward_message as router_forward,
    send_template_message as router_template
)

logger = logging.getLogger(__name__)

# LEGACY SHIMS: 
# Every function here now delegates to message_router.py.
# This prevents breaking existing code while migrating imports.

async def send_message(to: str, body: str, client_id: str = None) -> dict | None:
    # Build a minimal config for the router if only client_id is passed
    client_config = {"id": client_id} if client_id else None
    return await router_send(to, body, client_config=client_config)

async def send_media(to: str, media_type: str, url: str, caption: str = "", client_id: str = None) -> dict | None:
    client_config = {"id": client_id} if client_id else None
    return await router_media(to, url, media_type, caption, client_config=client_config)

async def send_reaction(to: str, emoji: str, original_msg_id: str) -> dict | None:
    # Reaction doesn't have a unified router path yet, keep as is or skip
    logger.debug("[Messaging] Reactions not yet routed via message_router.py")
    return None

async def mark_as_read(conversation_id: str, message_id: str) -> bool:
    return await router_read(conversation_id, message_id)

async def send_chunked_messages(
    to: str, 
    chunks: List[str], 
    incoming_text: str = "", 
    last_message_ts: float = 0, 
    message_id: str = "",
    client_id: str = None
) -> None:
    client_config = {"id": client_id} if client_id else None
    return await router_chunked(to, chunks, client_config, incoming_text, last_message_ts, message_id)

async def send_typing_indicator(to: str, conversation_id: str = "", message_id: str = "", client_id: str = None) -> bool:
    client_config = {"id": client_id} if client_id else None
    return await router_typing(to, message_id, client_config=client_config)

async def send_poll(to: str, question: str, options: List[str], client_id: str = None) -> dict | None:
    client_config = {"id": client_id} if client_id else None
    return await router_poll(to, question, options, client_config=client_config)

async def forward_message(to: str, original_msg_id: str, forward_to: str) -> dict | None:
    return await router_forward(to, original_msg_id, forward_to)

async def send_contact(to: str, contact_name: str, contact_phone: str) -> dict | None:
    logger.debug("[Messaging] send_contact not yet routed via message_router.py")
    return None

async def get_contact_phone(contact_id: str) -> str | None:
    return None
