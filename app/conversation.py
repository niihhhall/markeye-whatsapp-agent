import asyncio
import logging
import re
import json
from app.config import settings
from app.llm import llm_client
from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.models import ConversationState
from app.messaging import (
    send_message, 
    send_chunked_messages, 
    send_typing_indicator, 
    mark_as_read,
    send_poll,
    send_media,
    forward_message
)
from app.chunker import chunk_message, calculate_typing_delay
from app.state_machine import check_transition
from app.bant import extract_bant
from app.knowledge import retrieve_knowledge
from app.semantic_cache import semantic_cache
from typing import Dict, Any, List

from app.tracker import MarkTracker
from app.signals import (
    detect_interest_level, 
    detect_personality_type, 
    get_approach_instructions
)

logger = logging.getLogger(__name__)
tracker = MarkTracker()

from datetime import datetime, timezone
import random
from app.client_manager import client_manager
from app.training_api import compile_training_data
from app.bant import handle_bant_extraction
from app.semantic_cache import semantic_cache

async def process_conversation(
    phone: str,
    message: str,
    conversation_id: str = "",
    message_id: str = "",
    last_message_ts: float = 0,
    client_id: str = None,
):
    """
    Main conversation entry point.
    Delegates to the LangGraph StateGraph (app/graph.py) which handles
    all orchestration: session loading, stage classification, LLM generation,
    tool execution, and session persistence.
    """
    try:
        from app.graph import workflow

        initial_state = {
            "phone": phone,
            "message": message,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "last_message_ts": last_message_ts,
            "client_id": client_id,
            # Populated by load_context node:
            "session": {},
            "lead_data": {},
            "lead_id": None,
            "client_config": None,
            "knowledge_context": "",
            "response_text": "",
            "tool_calls": [],
            "should_exit": False,
            "exit_reason": "",
        }

        print(f"\n[Conversation] 🚀 Invoking graph for {phone}: '{message[:50]}...'", flush=True)
        await workflow.ainvoke(initial_state)

    except Exception as e:
        logger.critical("[Conversation] 🚨 CRITICAL ERROR processing %s: %s", phone, e, exc_info=True)
        from app.redis_client import redis_client
        await redis_client.clear_generating(phone)




async def on_conversation_end(phone: str, outcome: str, session: dict, lead_id: str = None):
    """Trigger scoring and training data collection when a conversation ends."""
    try:
        from app.conversation_scorer import score_conversation, save_for_training
        
        history = session.get("history", [])
        if not history:
            return
            
        score_results = await score_conversation(history, outcome)
        if score_results.get("worthy"):
            await save_for_training(redis_client.redis, phone, history, score_results, lead_id)
            
    except Exception as e:
        logger.error(f"[Conversation] ❌ Error in on_conversation_end for {phone}: {e}")
    
    # NEW: Trigger auto-compile for training in background after ANY conversation end event
    # The compiler will filter for Booker/Lost status or 24h stale
    try:
        asyncio.create_task(compile_training_data())
    except Exception as e:
        logger.error(f"[Conversation] ❌ Error triggering auto-compile: {e}")


async def check_low_content(phone: str, message: str, session: dict) -> bool:
    """
    Checks for low-content spam.
    IMPORTANT: In OPENING state, we NEVER put a lead into WAITING —
    "Hey" is a valid way to start a conversation.
    Spam protection only kicks in after DISCOVERY.
    """
    current_state = session.get("state", ConversationState.OPENING)
    
    # RULE: Never spam-filter new clients. Let Mark greet them naturally.
    if current_state == ConversationState.OPENING:
        return False
    
    content = message.strip().lower().rstrip("!?.")
    words = content.split()
    low_content_patterns = ["hey", "heyy", "heyyy", "hi", "hello", "yo", "sup", "?", "ok", "k", "yeah", "nice"]
    
    is_low_content = (len(words) < 2 and content in low_content_patterns) or len(words) == 0
    
    if is_low_content:
        count = session.get("low_content_count", 0) + 1
        session["low_content_count"] = count
        
        # Tier 1 (2 low-content messages): Casual re-engage (Master Prompt Fix 8)
        if count == 2:
            await send_message(phone, "Haha what's up, you good?")
            return True
        
        # Tier 2 (3+ messages): State transition to WAITING (Master Prompt Fix 4)
        if count >= 3:
            session["state"] = ConversationState.WAITING
            await redis_client.save_session(phone, session)
            await send_message(phone, "Hey, timing might be off. I'm here whenever you want to have a proper chat.")
            return True
            
    else:
        # Reset count on substantial message
        session["low_content_count"] = 0
    
    return False


async def check_and_send_calendly(phone: str, text: str, session: dict, client_config: dict = None) -> str:
    """
    Tracks if Calendly link was sent.
    We no longer block resending it if the user explicitly asks for it again.
    """
    calendly_link = client_config.get("calendly_link") if client_config else settings.CALENDLY_LINK
    
    if calendly_link and calendly_link in text:
        if not await redis_client.has_sent_calendly(phone):
            await redis_client.mark_calendly_sent(phone)
            logger.info("[Conversation] Tracking Calendly link sent to %s", phone)

    return text


async def build_enhanced_context(session: dict, lead_data: dict, message: str, knowledge_context: str = "", client_config: dict = None) -> list:
    """Builds enhanced LLM context with BANT, Form data and Knowledge base context."""
    
    # 0. Live Booking Verification (New)
    # If the user mentions booking, or we are in a booking-related state, 
    # re-fetch the absolute truth from the database to avoid Redis lag or stale cache.
    msg_low = message.lower()
    booking_keywords = [
        "booked", "done", "scheduled", "appointment", "calendar", "confirm",
        "sorted", "just did", "all set", "locked in", "reserved", "signed up",
        "filled in", "submitted", "completed", "went through", "i did it",
        "its booked", "just booked", "ive booked", "booking confirmed"
    ]
    
    lead_id = lead_data.get("id")
    live_state = session.get("state")
    latest_booking_info = None
    is_new_booking = False
    
    if lead_id and (any(kw in msg_low for kw in booking_keywords) or session.get("state") in [ConversationState.BOOKING, ConversationState.CONFIRMED]):
        logger.info("[Conversation] 🔍 Performing live booking check for %s", lead_id)
        db_state = await tracker.get_conversation_state(lead_id)
        if db_state:
            session["state"] = db_state.get("current_state", session["state"])
            live_state = session["state"]
        
        latest_booking = await tracker.get_latest_booking(lead_id)
        if latest_booking:
            created_at_str = latest_booking.get("created_at")
            if created_at_str:
                try:
                    # Created_at is usually ISO format like "2024-03-20T14:30:00+00:00"
                    booking_time = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    diff = (now - booking_time).total_seconds()
                    
                    # Freshness Check: Is this a NEW booking made in the last 15 minutes?
                    is_new_booking = diff < 900 # 15 minutes
                    
                    latest_booking_info = (
                        f"Latest booking: {created_at_str} "
                        f"(Status: {latest_booking.get('status')}, "
                        f"NEW_BOOKING_JUST_CONFIRMED: {str(is_new_booking).upper()})"
                    )
                except Exception as e:
                    logger.error(f"[Conversation] Error parsing booking date: {e}")
                    latest_booking_info = f"Latest booking: {created_at_str} (Status: {latest_booking.get('status')})"

    # Pass everything to llm_client
    messages = await llm_client.build_context(session, lead_data, message, knowledge_context, client_config=client_config)
    
    # Add the latest_booking_info to the system prompt if found
    if latest_booking_info and messages and messages[0]["role"] == "system":
        messages[0]["content"] += f"\n\n═══ LIVE SYSTEM DATA ═══\n{latest_booking_info}\n"
        if is_new_booking:
            messages[0]["content"] += "IMPORTANT: A new booking was just detected in the system within the last 15 minutes. You MUST acknowledge this.\n"
        else:
            messages[0]["content"] += "IMPORTANT: No new booking found in the last 15 minutes. If the user claims they just booked, they are lying or the system hasn't updated. Tell them to wait a second or try again.\n"
    elif any(kw in msg_low for kw in booking_keywords) and messages and messages[0]["role"] == "system":
        # Lead mentioned booking but NO booking data exists at all in the database
        messages[0]["content"] += (
            "\n\n═══ LIVE SYSTEM DATA ═══\n"
            "No bookings found in the system for this lead.\n"
            "NEW_BOOKING_JUST_CONFIRMED: FALSE\n"
            "IMPORTANT: The lead may claim they booked but the system has ZERO record of any booking. "
            "Do NOT confirm. Ask them to try the link again. Say something like: "
            "'hmm nothing's come through on my end yet, give it a sec or try the link again'\n"
        )

    # 1. Fetch Relevant RAG Context
    rag_map = {
        ConversationState.OPENING: "rag:sales:psychology",
        ConversationState.DISCOVERY: "rag:sales:spin",
        ConversationState.QUALIFICATION: "rag:sales:signals",
        ConversationState.BOOKING: "rag:sales:closing",
    }
    # Convert ConversationState enum to string for map lookup, default to OPENING if not found
    current_state_str = session.get("state", ConversationState.OPENING)
    rag_key = rag_map.get(current_state_str, "rag:sales:psychology")
    
    # Check for likely objections (simple heuristic before LLM)
    msg_low = message.lower()
    if any(o in msg_low for o in ["expensive", "cost", "price", "budget", "time", "busy", "think", "team", "va"]):
        rag_key = "rag:sales:objections"

    rag_training = await redis_client.get(rag_key) or ""

    # Qualification signaling
    bant_scores = session.get("bant_scores", {})
    overall_score = bant_scores.get("overall_score", 0)
    recommended_action = bant_scores.get("recommended_action", "continue_discovery")
    
    # 1. Detect Buyer Signals and Personality
    interest = detect_interest_level(message)
    user_history = [m["content"] for m in session.get("history", []) if m["role"] == "user"]
    personality = detect_personality_type(user_history)
    approach = get_approach_instructions(interest, personality)

    # 2. Base Instruction (BANT + Action)
    instruction = f"\n\nCURRENT BANT STATUS: Score {overall_score}/10. Action: {recommended_action}.\n"
    if recommended_action == "continue_discovery" or overall_score < 7:
        instruction += "INSTRUCTION: Maintain Chat Mode. Use SPIN questions only if they flow naturally. Do NOT force discovery.\n"
    elif overall_score >= 7:
        instruction += "INSTRUCTION: Lead is qualified. Suggest a call with Markeye when the moment feels natural. Suggest it as a logical next step to solve their problem.\n"
    
    # 3. Dynamic Approach Instruction
    instruction += approach + "\n"
    
    # 4. Inject Form context (Issue 9)
    form_keys = ["name", "email", "company", "industry", "message", "lead_source", "website", "company_size", "role"]
    form_details = []
    for k in form_keys:
        val = lead_data.get(k)
        if val:
            form_details.append(f"{k.replace('_', ' ').capitalize()}: {val}")
    
    if form_details:
        instruction += f"\nFORM DATA SUBMITTED BY LEAD:\n" + "\n".join(form_details) + "\nThis is ONLY background context for you. Do NOT assume anything from this data. Do NOT reference their industry, lead volume, problems, or terminology unless THEY mention it first in the conversation. The industry field tells you their general space, nothing else. Never say things like 'property enquiries', 'discovery calls', 'viewings', 'qualifying leads', or any industry-specific term unless the lead used that term first. Always ask what they do and what they need. NEVER skip discovery questions based on form data. If you catch yourself about to reference something from form data that the lead hasn't said, STOP and ask a genuine question instead.\n"

    # Append everything to the system message
    if messages and messages[0]["role"] == "system":
        if rag_training:
            messages[0]["content"] += f"\n\n--- SALES TRAINING MODULE ---\n{rag_training}\n"
        messages[0]["content"] += instruction
        
    return messages
