import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from app.config import settings
from app.models import ConversationState
from app.redis_client import redis_client
from app.tracker import MarkTracker
from app.llm import llm_client
from app.signals import (
    detect_interest_level, 
    detect_personality_type, 
    get_approach_instructions
)

logger = logging.getLogger(__name__)
tracker = MarkTracker()

async def on_conversation_end(phone: str, outcome: str, session: dict, lead_id: str = None):
    """Trigger scoring and training data collection when a conversation ends."""
    try:
        from app.conversation_scorer import score_conversation, save_for_training
        from app.training_api import compile_training_data
        
        history = session.get("history", [])
        if not history:
            return
            
        score_results = await score_conversation(history, outcome)
        if score_results.get("worthy"):
            await save_for_training(redis_client.redis, phone, history, score_results, lead_id)
            
        asyncio.create_task(compile_training_data())
    except Exception as e:
        logger.error(f"[GraphUtils] Error in on_conversation_end: {e}")

async def check_and_send_calendly(phone: str, text: str, session: dict, client_config: dict = None) -> str:
    """Tracks if the booking link was sent."""
    # Fix 12: Use settings.booking_link
    booking_link = client_config.get("calendly_link") or client_config.get("calcom_link") if client_config else settings.booking_link
    
    if booking_link and booking_link in text:
        if not await redis_client.has_sent_calendly(phone):
            await redis_client.mark_calendly_sent(phone)
            logger.info("[GraphUtils] Tracking booking link sent to %s", phone)
    return text

async def build_enhanced_context(session: dict, lead_data: dict, message: str, knowledge_context: str = "", client_config: dict = None) -> list:
    """Builds enhanced LLM context with BANT, Form data and Knowledge base context."""
    msg_low = message.lower()
    booking_keywords = ["booked", "done", "scheduled", "appointment", "calendar", "confirm"]
    
    lead_id = lead_data.get("id")
    latest_booking_info = None
    is_new_booking = False
    
    if lead_id and (any(kw in msg_low for kw in booking_keywords) or session.get("state") in [ConversationState.BOOKING, ConversationState.CONFIRMED]):
        latest_booking = await tracker.get_latest_booking(lead_id)
        if latest_booking:
            created_at_str = latest_booking.get("created_at")
            if created_at_str:
                try:
                    booking_time = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    diff = (datetime.now(timezone.utc) - booking_time).total_seconds()
                    is_new_booking = diff < 900
                    latest_booking_info = f"Latest booking: {created_at_str} (NEW: {is_new_booking})"
                except:
                    latest_booking_info = f"Latest booking: {created_at_str}"

    messages = await llm_client.build_context(session, lead_data, message, knowledge_context, client_config=client_config)
    
    # Qualification signaling
    bant_scores = session.get("bant_scores", {})
    overall_score = bant_scores.get("overall_score", 0)
    
    # Personality approach
    interest = detect_interest_level(message)
    user_history = [m["content"] for m in session.get("history", []) if m["role"] == "user"]
    personality = detect_personality_type(user_history)
    approach = get_approach_instructions(interest, personality)

    instruction = f"\n\nCURRENT BANT STATUS: Score {overall_score}/10. Approach: {personality}.\n{approach}\n"
    
    if messages and messages[0]["role"] == "system":
        messages[0]["content"] += instruction
        if latest_booking_info:
            messages[0]["content"] += f"\nSYSTEM DATA: {latest_booking_info}\n"
        
    return messages
