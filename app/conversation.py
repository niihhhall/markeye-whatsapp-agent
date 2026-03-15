import asyncio
import logging
from app.config import settings
from app.llm import llm_client
from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.messaging import send_message, send_chunked_messages, send_typing_indicator
from app.chunker import chunk_message, calculate_typing_delay
from app.state_machine import check_transition
from app.bant import extract_bant
from app.models import ConversationState
from typing import Dict, Any

from app.tracker import AlbertTracker

logger = logging.getLogger(__name__)
tracker = AlbertTracker()

from datetime import datetime, timezone
import random
from app.signals import detect_interest_level, detect_personality_type, get_approach_instructions

async def process_conversation(phone: str, message: str, conversation_id: str = "", message_id: str = ""):
    """Main conversation engine logic."""
    try:
        print(f"\n[Conversation] 🚀 Starting process for {phone}: '{message[:50]}...'", flush=True)
        logger.info("\n[Conversation] 🚀 Starting process for %s: '%s...'", phone, message[:50])

        # Step 1: Initial wait (3s rolling + 2s here = 5s total)
        await asyncio.sleep(2)
        
        # Step 2: Send read receipt (blue ticks) at 5s mark
        if message_id:
            from app.messaging import mark_as_read
            print(f"[Conversation] ✅ Sending blue ticks for {phone}", flush=True)
            await mark_as_read(conversation_id, message_id)

        # Step 3: Random pause before typing (3-5s)
        extra_pause = random.uniform(3, 5)
        print(f"[Conversation] ⏳ Waiting {extra_pause:.1f}s before typing start", flush=True)
        await asyncio.sleep(extra_pause)

        # Step 4: Get session and lead data
        session = await redis_client.get_session(phone)
        if not session:
            lead = tracker.get_lead_by_phone(phone)
            if not lead:
                lead = tracker.create_lead(phone=phone)
            session = {
                "state": ConversationState.OPENING,
                "history": [],
                "turn_count": 0,
                "lead_data": lead or {"phone": phone},
                "low_content_count": 0
            }
        
        lead_data = session.get("lead_data", {})
        lead_id = lead_data.get("id")

        # Step 5: Start Typing Indicator (Simulates "Writing...")
        # We start this BEFORE LLM call so the user knows we are responding
        print(f"[Conversation] ✍️ Starting typing simulation for {phone}", flush=True)
        await send_typing_indicator(phone, conversation_id, message_id)
        if lead_id:
            tracker.set_typing_status(lead_id, True)

        # Step 6: Set processing flag
        await redis_client.set_processing(phone, True)

        # Step 7: Check if message is low-content spam
        is_spam = await check_low_content(phone, message, session)
        if is_spam:
            return

        # Step 8: Knowledge Base Retrieval (RAG)
        from app.knowledge import retrieve_knowledge
        print(f"[Conversation] 🔍 Searching knowledge base for: {phone}", flush=True)
        knowledge_context = await retrieve_knowledge(message)
        if knowledge_context:
            print(f"[Conversation] 📚 Found knowledge context for {phone}", flush=True)

        # Step 9: LLM Call
        messages = await build_enhanced_context(session, lead_data, message, knowledge_context)
        response_text = await llm_client.call_llm(
            messages,
            model=settings.OPENROUTER_PRIMARY_MODEL,
            lead_id=lead_id,
            conversation_state=session["state"],
            phone=phone,
            company=lead_data.get("company", "")
        )
        print(f"[Conversation] 🤖 LLM Response generated for {phone}", flush=True)
        
        if not response_text or "[NO_REPLY]" in response_text.upper():
            if response_text and "[NO_REPLY]" in response_text.upper():
                logger.info("[Conversation] LLM generated [NO_REPLY] for %s. Ignoring and doing nothing.", phone)
            await redis_client.set_processing(phone, False)
            return

        # Step 9.5: Clean Response (Strip any system tags like [SYSTEM ACTION: ...])
        import re
        original_response = response_text
        response_text = re.sub(r'\[[A-Z\s_]+:?.*?\]', '', response_text).strip()
        if original_response != response_text:
            logger.info("[Conversation] Stripped system tags from response for %s", phone)

        # Step 9: Interrupt Check — did new messages arrive during LLM call?
        new_messages_str = await redis_client.get_and_clear_buffer(phone)
        if new_messages_str:
            logger.info("[Conversation] New messages arrived during processing for %s, re-generating", phone)
            combined = message + "\n" + new_messages_str
            await redis_client.set_processing(phone, False)
            # Re-process with combined input
            return await process_conversation(phone, combined, conversation_id, message_id)

        # Step 10: Calendly Resend Logic (>10 message distance)
        response_text = await check_and_send_calendly(phone, response_text, session["history"])

        # Step 11: Send natural multi-bubble response
        if response_text:
            print(f"[Conversation] 📤 Splitting and sending multi-bubble response to {phone}", flush=True)
            from app.chunker import chunk_message
            chunks = chunk_message(response_text)
            
            # Use the existing utility that handles delays and typing indicators
            from app.messaging import send_chunked_messages
            await send_chunked_messages(phone, chunks, conversation_id, message_id)
            
            if lead_id:
                tracker.set_typing_status(lead_id, False)

        # Step 12: Update session history and turn count
        session["history"].append({"role": "user", "content": message})
        session["history"].append({"role": "assistant", "content": response_text})
        session["history"] = session["history"][-10:]
        session["turn_count"] += 1
        session["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Step 13: Tracking outbound
        tracker.log_outbound(lead_id, response_text)

        # Step 14: Check for state transition
        new_state = check_transition(session["state"], session)
        
        # Detect Exit Phrases for CLOSED state (Issue 7 & 8)
        exit_phrases = [
            "no worries, you know where to find us",
            "come back when you want to chat properly",
            "all the best",
            "leave it there"
        ]
        response_lower = response_text.lower()
        if any(phrase in response_lower for phrase in exit_phrases):
            logger.info("[Conversation] Exit phrase detected, closing conversation for %s", phone)
            new_state = ConversationState.CLOSED

        if new_state and new_state != session["state"]:
            logger.info("[Conversation] Transitioning state: %s -> %s", session['state'], new_state)
            session["state"] = new_state
            
            state_map = {
                ConversationState.OPENING: "Opening",
                ConversationState.DISCOVERY: "Discovery",
                ConversationState.QUALIFICATION: "Qualification",
                ConversationState.BOOKING: "Booking Push",
                ConversationState.ESCALATION: "Escalation",
                ConversationState.CONFIRMED: "Confirmed",
                ConversationState.WAITING: "Waiting",
                ConversationState.CLOSED: "Closed"
            }
            tracker.update_state(lead_id, state_map.get(new_state, "Opening"))

        # Step 15: Cleanup and background tasks
        await redis_client.save_session(phone, session)
        await redis_client.set_processing(phone, False)
        asyncio.create_task(extract_bant(phone, session["history"]))

    except Exception as e:
        logger.critical("[Conversation] 🚨 CRITICAL ERROR processing %s: %s", phone, e, exc_info=True)
        await redis_client.set_processing(phone, False)


async def check_low_content(phone: str, message: str, session: dict) -> bool:
    """
    Checks for low-content spam.
    IMPORTANT: In OPENING state, we NEVER put a lead into WAITING —
    "Hey" is a valid way to start a conversation.
    Spam protection only kicks in after DISCOVERY.
    """
    current_state = session.get("state", ConversationState.OPENING)
    
    # RULE: Never spam-filter new clients. Let Albert greet them naturally.
    if current_state == ConversationState.OPENING:
        return False
    
    content = message.strip().lower().rstrip("!?.")
    words = content.split()
    low_content_patterns = ["hey", "heyy", "heyyy", "hi", "hello", "yo", "sup", "?", "ok", "k", "yeah", "nice"]
    
    is_low_content = (len(words) < 2 and content in low_content_patterns) or len(words) == 0
    
    if is_low_content:
        count = session.get("low_content_count", 0) + 1
        session["low_content_count"] = count
        
        # Tier 1 (3rd message): Casual re-engage
        if count == 3:
            from app.messaging import send_message
            await send_message(phone, "Haha what's up, you good?")
            return True
        
        # Tier 2 (6+ messages): State transition to WAITING
        if count >= 6:
            session["state"] = ConversationState.WAITING
            await redis_client.save_session(phone, session)
            from app.messaging import send_message
            await send_message(phone, "Hey, timing might be off. I'm here whenever you want to have a proper chat.")
            return True
            
    else:
        # Reset count on substantial message
        session["low_content_count"] = 0
    
    return False


async def check_and_send_calendly(phone: str, text: str, history: list) -> str:
    """
    Ensures Calendly link is tracked if sent.
    We removed the strict backend replacement so the AI doesn't sound robotic with '[link provided above]'.
    """
    calendly_link = settings.CALENDLY_LINK
    
    if calendly_link in text:
        await redis_client.mark_calendly_sent(phone)
        logger.info("[Conversation] Tracking Calendly link sent to %s", phone)

    return text


async def build_enhanced_context(session: dict, lead_data: dict, message: str, knowledge_context: str = "") -> list:
    """Builds enhanced LLM context with BANT, Form data and Knowledge base context."""
    messages = await llm_client.build_context(session, lead_data, message, knowledge_context)
    
    # Extract existing system prompt to append/pre-pend if needed, 
    # but llm_client.build_context already reads system_prompt.txt.
    # We will let the placeholders in system_prompt.txt handle it,
    # but we can add an extra "INSTRUCTION" block here for dynamic guidance.
    
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
        instruction += "INSTRUCTION: Do NOT suggest a call yet. Keep discovering. You need more information.\n"
    elif overall_score >= 7:
        instruction += "INSTRUCTION: Lead is qualified. Suggest a call with Louis when the moment feels natural.\n"
    
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
        instruction += f"\nFORM DATA SUBMITTED BY LEAD:\n" + "\n".join(form_details) + "\nUse this information to skip discovery questions we already have answers for.\n"

    # Append instruction to the system message
    if messages and messages[0]["role"] == "system":
        messages[0]["content"] += instruction
        
    return messages
