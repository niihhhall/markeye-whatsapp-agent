from typing import Optional, Dict, Any
import logging
import os
from app.models import ConversationState

logger = logging.getLogger(__name__)

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


async def classify_stage_with_llm(
    history: list,
    current_state: ConversationState,
) -> Optional[ConversationState]:
    """
    SalesGPT-style dual-chain stage classifier.
    Uses a cheap LLM call to semantically determine conversation stage
    from history — separate from the response generator.
    Falls back to None (caller keeps rule-based state) on any failure.
    """
    if not history or len(history) < 2:
        return None  # Not enough context for meaningful classification

    from app.llm_router import llm_router
    from app.config import settings

    prompt_path = os.path.join(os.getcwd(), "prompts", "stage_classifier.txt")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            classifier_prompt = f.read()
    except FileNotFoundError:
        logger.warning("[StateMachine] stage_classifier.txt not found — skipping LLM classification.")
        return None

    # Format last 10 turns for context
    recent = history[-10:]
    history_text = "\n".join(
        f"{'Lead' if m['role'] == 'user' else 'Mark'}: {m['content']}"
        for m in recent
    )

    prompt = (
        classifier_prompt
        .replace("{{history}}", history_text)
        .replace("{{current_stage}}", str(current_state))
    )

    try:
        result = await llm_router.generate_completion(
            messages=[{"role": "user", "content": prompt}],
            model_override=settings.GEMINI_MODEL,
            timeout=6.0,  # Changed from 3.0 — Gemini needs headroom
        )
        stage_str = result["content"].strip().upper()

        stage_map = {
            "OPENING":       ConversationState.OPENING,
            "DISCOVERY":     ConversationState.DISCOVERY,
            "QUALIFICATION": ConversationState.QUALIFICATION,
            "BOOKING":       ConversationState.BOOKING,
            "CONFIRMED":     ConversationState.CONFIRMED,
            "CLOSED":        ConversationState.CLOSED,
            "WAITING":       ConversationState.WAITING,
            "ESCALATION":    ConversationState.ESCALATION,
        }

        classified = stage_map.get(stage_str)
        if classified:
            logger.info("[StateMachine] LLM classified stage: %s (was: %s)", classified, current_state)
        return classified

    except Exception as e:
        logger.warning("[StateMachine] LLM stage classifier error: %s", e)
        # Track classifier timeouts for monitoring
        try:
            from app.redis_client import redis_client
            await redis_client.redis.incr("metrics:stage_classifier_failures")
        except Exception:
            pass
        return None


def check_transition(current_state: ConversationState, session_data: Dict[str, Any], client_config: dict = None) -> Optional[ConversationState]:
    """Evaluates whether a rule-based transition should happen."""
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
        threshold = (
            criteria.get("overall_threshold_mark")
            or criteria.get("overall_threshold")
            or 7
        )

    if turn_count < rule["min_turns"]:
        return None

    if current_state == ConversationState.OPENING:
        # Opening to Discovery happens after first reply
        return rule["next"]

    if current_state == ConversationState.DISCOVERY:
        # Discovery to Qualification after 3+ turns AND some BANT info
        if turn_count >= 3 and overall_score > 0:
            return rule["next"]

    if current_state == ConversationState.QUALIFICATION:
        # Qualification to Booking if BANT score >= threshold (default 7)
        if overall_score >= threshold:
            return ConversationState.BOOKING

    return None
