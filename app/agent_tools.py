"""
Agent tool definitions and per-state whitelists.

Implements two patterns from the Elite AI SDR Reference Stack:
  - Knotie-AI: Structured tool dispatch instead of bracket-token string parsing
  - llmstatemachine: Per-state tool whitelist — LLM only sees tools valid for the current stage
"""
import json
import logging
import re
from typing import List, Optional
from app.models import ConversationState

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Tool Definitions (OpenAI-compatible schema for documentation + future calls)
# ─────────────────────────────────────────────────────────────────────────────

SALES_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_booking_poll",
            "description": (
                "Send a WhatsApp poll asking the lead to pick a time slot for a discovery call. "
                "Use when lead shows buying intent but hasn't committed to a specific booking."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_booking_link",
            "description": (
                "Send the Calendly/Cal.com booking link directly. "
                "Use when lead explicitly asks for a link or is clearly ready to book."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_pricing_doc",
            "description": (
                "Send the Markeye pricing PDF document. "
                "Use ONLY when lead explicitly asks about pricing, cost, or investment."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Escalate the conversation to a human sales rep immediately. "
                "Use when: lead is highly frustrated, explicitly asks for a human, BANT score ≥ 9, "
                "or the situation requires human judgement."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_conversation",
            "description": (
                "Mark the conversation as CLOSED. "
                "Use when lead explicitly says they're not interested, asks to stop, "
                "or the conversation has reached a clear endpoint."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Per-State Tool Whitelist  (llmstatemachine pattern)
# LLM only sees tools that are valid for the current conversation state.
# This eliminates erratic behaviour like booking during OPENING.
# ─────────────────────────────────────────────────────────────────────────────

STATE_TOOL_WHITELIST: dict = {
    ConversationState.OPENING:       [],                # No tools — just greet naturally
    ConversationState.DISCOVERY:     ["escalate_to_human", "close_conversation"],
    ConversationState.QUALIFICATION: ["escalate_to_human", "send_booking_poll", "close_conversation"],
    ConversationState.BOOKING:       ["send_booking_link", "send_pricing_doc", "send_booking_poll", "escalate_to_human", "close_conversation"],
    ConversationState.CONFIRMED:     ["close_conversation"],
    ConversationState.WAITING:       ["close_conversation"],
    ConversationState.CLOSED:        [],
    ConversationState.ESCALATION:    ["escalate_to_human"],
}


def get_tools_for_state(state: ConversationState) -> List[dict]:
    """Returns only the tool schemas allowed in the given conversation state."""
    allowed = STATE_TOOL_WHITELIST.get(state, [])
    if not allowed:
        return []
    return [t for t in SALES_TOOLS if t["function"]["name"] in allowed]


# ─────────────────────────────────────────────────────────────────────────────
# Tool Classifier  (replaces bracket-token regex scanning)
# Uses a cheap LLM call to decide which tools should fire for this turn.
# ─────────────────────────────────────────────────────────────────────────────

async def classify_tools(
    session: dict,
    user_message: str,
    assistant_reply: str,
) -> List[str]:
    """
    Uses a cheap LLM call to determine which tools to fire.
    Returns a list of tool name strings filtered to the current state whitelist.
    Falls back to legacy bracket scanning if LLM fails.
    """
    from app.llm_router import llm_router
    from app.config import settings

    current_state = session.get("state", ConversationState.DISCOVERY)
    allowed = STATE_TOOL_WHITELIST.get(current_state, [])
    if not allowed:
        return []

    bant_score = session.get("bant_scores", {}).get("overall_score", 0)

    prompt = f"""You are deciding what ACTIONS the sales assistant should take after its reply.

Conversation state: {current_state}
Lead's message: {user_message}
Assistant's reply: {assistant_reply}
BANT qualification score: {bant_score}/10

Available actions for this state: {allowed}

Which of the above actions should be triggered RIGHT NOW? Be conservative — most turns need no action.
Respond with ONLY a JSON object: {{"tools": ["action_name"]}} or {{"tools": []}} if no action needed."""

    try:
        result = await llm_router.generate_completion(
            messages=[{"role": "user", "content": prompt}],
            model_override=settings.GEMINI_MODEL,
            timeout=4.0,
        )
        content = result["content"].strip()

        # Try parsing as JSON
        try:
            data = json.loads(content)
            tools = data.get("tools", data) if isinstance(data, dict) else data
            if isinstance(tools, list):
                return [t for t in tools if t in allowed and isinstance(t, str)]
        except json.JSONDecodeError:
            # Try to extract array from text
            match = re.search(r'\[([^\]]*)\]', content)
            if match:
                try:
                    items = json.loads(f"[{match.group(1)}]")
                    return [t for t in items if t in allowed and isinstance(t, str)]
                except Exception:
                    pass

    except Exception as e:
        logger.warning("[AgentTools] LLM tool classifier failed: %s — falling back to legacy scanner.", e)

    # ── Legacy bracket-token fallback ─────────────────────────────────────
    legacy_map = {
        "send_booking_poll": r'\[(?:SEND_)?BOOKING[\s_]?POLL\]',
        "send_booking_link": r'\[(?:SEND_)?(?:CALENDLY|BOOKING[\s_]?LINK)\]',
        "send_pricing_doc":  r'\[(?:SEND_)?PRICING\]',
        "escalate_to_human": r'\[ESCALATE\]',
        "close_conversation": r'\[CLOSE\]',
    }
    fired = []
    combined = user_message + " " + assistant_reply
    for tool_name, pattern in legacy_map.items():
        if tool_name in allowed and re.search(pattern, combined, re.I):
            fired.append(tool_name)
    return fired


# ─────────────────────────────────────────────────────────────────────────────
# Tool Executor
# ─────────────────────────────────────────────────────────────────────────────

async def execute_tool_call(
    tool_name: str,
    phone: str,
    message_id: str = "",
    session: Optional[dict] = None,
    client_config: Optional[dict] = None,
) -> None:
    """Dispatch tool execution by name."""
    from app.messaging import send_poll, send_media, forward_message, send_typing_indicator
    from app.config import settings
    from app.redis_client import redis_client

    logger.info("[AgentTools] Executing tool '%s' for %s", tool_name, phone)

    client_id = client_config.get("id") if client_config else None

    if tool_name == "send_booking_poll":
        await send_poll(
            to=phone,
            question="Want to book a quick 15-min discovery call? Pick what works:",
            options=["Today", "Tomorrow", "This Week", "Not Yet"],
            client_id=client_id
        )

    elif tool_name == "send_booking_link":
        # Link is embedded in response_text by the LLM; just track it was sent
        await redis_client.mark_calendly_sent(phone)

    elif tool_name == "send_pricing_doc":
        pricing_url = (client_config.get("settings") or {}).get("pricing_url") or settings.PRICING_PDF_URL
        await send_media(
            to=phone,
            media_type="document",
            url=pricing_url,
            caption="Markeye Pricing Overview",
            client_id=client_id
        )

    elif tool_name == "escalate_to_human":
        sales_number = client_config.get("sales_contact") or settings.SALES_PHONE_NUMBER
        if sales_number:
            await forward_message(
                to=phone,
                original_msg_id=message_id,
                forward_to=sales_number,
                client_id=client_id
            )

    elif tool_name == "close_conversation":
        from app.models import ConversationState as CS
        if session is not None:
            session["state"] = CS.CLOSED
            await redis_client.save_session(phone, session)
