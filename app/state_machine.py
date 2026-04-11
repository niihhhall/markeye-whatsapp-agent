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

def check_transition(current_state: ConversationState, session_data: Dict[str, Any], client_config: dict = None) -> Optional[ConversationState]:
    """Evaluates whether a transition should happen."""
    rule = TRANSITIONS.get(current_state)
    if not rule:
        return None

    turn_count = session_data.get("turn_count", 0)
    bant_scores = session_data.get("bant_scores", {})
    overall_score = bant_scores.get("overall_score", 0)

    # Resolve threshold (Client specific or global default 7)
    threshold = 7
    if client_config and client_config.get("bant_criteria"):
        criteria = client_config["bant_criteria"]
        threshold = criteria.get("overall_threshold_mark", 7) # Using a dedicated mark threshold
        if not threshold and criteria.get("overall_threshold"):
            # If they provided total threshold (e.g. 25/40), convert to 0-10 scale
            pass 
    
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
        # Qualification to Booking if BANT score >= threshold (default 7)
        if overall_score >= threshold:
            return ConversationState.BOOKING

    # Special handling for WAITING/CLOSED is usually manual or triggered by content
    # but we can return the current state as standard.
    
    return None
