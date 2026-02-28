from typing import Optional, Dict, Any
from app.models import ConversationState

# Transition rules
TRANSITIONS = {
    ConversationState.OPENING: {
        "next": ConversationState.DISCOVERY,
        "condition": "lead_replied",
        "min_turns": 1,
    },
    ConversationState.DISCOVERY: {
        "next": ConversationState.QUALIFICATION,
        "condition": "enough_context",
        "min_turns": 3,
    },
    ConversationState.QUALIFICATION: {
        "next": ConversationState.BOOKING,
        "condition": "bant_score_gte_7",
        "min_turns": 2,
    },
    ConversationState.BOOKING: {
        "next": ConversationState.CONFIRMED,
        "condition": "booking_confirmed",
        "min_turns": 1,
    },
}

def check_transition(current_state: ConversationState, session_data: Dict[str, Any]) -> Optional[ConversationState]:
    """Evaluates whether a transition should happen."""
    rule = TRANSITIONS.get(current_state)
    if not rule:
        return None

    turn_count = session_data.get("turn_count", 0)
    bant_scores = session_data.get("bant_scores", {})
    overall_score = bant_scores.get("overall_score", 0)

    if turn_count < rule["min_turns"]:
        return None

    if current_state == ConversationState.OPENING:
        # Opening to Discovery happens after first reply
        return rule["next"]

    if current_state == ConversationState.DISCOVERY:
        # Discovery to Qualification after 3+ turns
        if turn_count >= 3:
            return rule["next"]

    if current_state == ConversationState.QUALIFICATION:
        # Qualification to Booking if BANT score >= 7
        if overall_score >= 7:
            return rule["next"]

    return None
