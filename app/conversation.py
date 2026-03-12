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

async def process_conversation(phone: str, message: str, conversation_id: str = "", message_id: str = ""):
    """Main conversation engine logic."""
    try:
        print(f"\n[Conversation] 🚀 Starting process for {phone}: '{message[:50]}...'", flush=True)
        logger.info("\n[Conversation] 🚀 Starting process for %s: '%s...'", phone, message[:50])

        # Step 1: Wait 5 seconds, then send read receipt (blue ticks)
        await asyncio.sleep(5)
        if message_id and (conversation_id or settings.MESSAGING_PROVIDER == "whatsapp_cloud"):
            from app.messaging import mark_as_read
            await mark_as_read(conversation_id, message_id)

        # Step 2: Set processing flag and wait 3-5 seconds
        await redis_client.set_processing(phone, True)
        await asyncio.sleep(random.uniform(3, 5))

        # Step 3: Get session and lead data
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

        # Step 4: Check if message is low-content spam
        is_spam = await check_low_content(phone, message, session)
        if is_spam:
            return

        # Step 5: Simulate thinking with typing indicator
        print(f"[Conversation] 💭 Sending typing indicator to {phone}", flush=True)
        await send_typing_indicator(phone, conversation_id, message_id)

        # Step 6: LLM Call
        messages = await build_enhanced_context(session, lead_data, message)
        response_text = await llm_client.call_llm(
            messages,
            model=settings.OPENROUTER_PRIMARY_MODEL,
            lead_id=lead_id,
            conversation_state=session["state"],
            phone=phone,
            company=lead_data.get("company", "")
        )
        print(f"[Conversation] 🤖 LLM Response generated for {phone}", flush=True)
        
        if not response_text:
            await redis_client.set_processing(phone, False)
            return

        # Step 7: Interrupt Check — did new messages arrive during LLM call?
        new_buffer = await redis_client.lrange(f"buffer:{phone}", 0, -1)
        if new_buffer:
            logger.info("[Conversation] New messages arrived during processing for %s, re-generating", phone)
            combined = message + "\n" + "\n".join(new_buffer)
            await redis_client.get_and_clear_buffer(phone)
            await redis_client.set_processing(phone, False)
            # Re-process with combined input
            return await process_conversation(phone, combined, conversation_id, message_id)

        # Step 8: Calendly Once-Only Check
        response_text = await check_and_send_calendly(phone, response_text)

        # Step 9: Chunk and send response
        chunks = chunk_message(response_text)
        if chunks:
            initial_delay = calculate_typing_delay(chunks[0][:100])
            await asyncio.sleep(initial_delay)
            print(f"[Conversation] 📤 Sending {len(chunks)} chunks to {phone}", flush=True)
            await send_chunked_messages(phone, chunks, conversation_id)

        # Step 10: Update session history and turn count
        session["history"].append({"role": "user", "content": message})
        session["history"].append({"role": "assistant", "content": response_text})
        session["history"] = session["history"][-10:]
        session["turn_count"] += 1
        session["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Step 11: Tracking outbound
        for chunk in chunks:
            tracker.log_outbound(lead_id, chunk)

        # Step 12: Check for state transition
        new_state = check_transition(session["state"], session)
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

        # Step 13: Cleanup and background tasks
        await redis_client.save_session(phone, session)
        await redis_client.set_processing(phone, False)
        asyncio.create_task(extract_bant(phone, session["history"]))

    except Exception as e:
        logger.critical("[Conversation] 🚨 CRITICAL ERROR processing %s: %s", phone, e, exc_info=True)
        await redis_client.set_processing(phone, False)


async def check_low_content(phone: str, message: str, session: dict) -> bool:
    """Checks for low-content spam and handles WAITING state."""
    words = message.strip().split()
    low_content_patterns = ["hey", "heyy", "heyyy", "hi", "hello", "yo", "sup", "?", "ok", "k", "yeah"]
    
    is_low_content = (
        len(words) < 5 and 
        message.strip().lower().rstrip("!?.") in low_content_patterns
    ) or len(words) < 2
    
    if is_low_content:
        count = session.get("low_content_count", 0) + 1
        session["low_content_count"] = count
        
        if count >= settings.LOW_CONTENT_THRESHOLD:
            session["state"] = ConversationState.WAITING
            await redis_client.save_session(phone, session)
            await send_message(phone, "Hey, timing might be off. I'm here whenever you want to have a proper chat.")
            return True
    else:
        session["low_content_count"] = 0
    
    return False


async def check_and_send_calendly(phone: str, text: str) -> str:
    """Ensures Calendly link is only sent once."""
    calendly_link = settings.CALENDLY_LINK
    if calendly_link in text:
        if await redis_client.has_sent_calendly(phone):
            text = text.replace(calendly_link, "[link already sent above]")
            logger.info("[Conversation] Calendly link already sent to %s, removing", phone)
        else:
            await redis_client.mark_calendly_sent(phone)
    return text


async def build_enhanced_context(session: dict, lead_data: dict, message: str) -> list:
    """Builds enhanced LLM context with BANT and Form data."""
    messages = await llm_client.build_context(session, lead_data, message)
    
    # Extract existing system prompt to append/pre-pend if needed, 
    # but llm_client.build_context already reads system_prompt.txt.
    # We will let the placeholders in system_prompt.txt handle it,
    # but we can add an extra "INSTRUCTION" block here for dynamic guidance.
    
    bant_scores = session.get("bant_scores", {})
    overall_score = bant_scores.get("overall_score", 0)
    recommended_action = bant_scores.get("recommended_action", "continue_discovery")
    
    # Qualification signaling
    has_signals = False
    if lead_data.get("lead_source") and lead_data.get("industry") and session.get("turn_count", 0) > 2:
        has_signals = True # Simplification for logic

    instruction = f"\n\nCURRENT BANT STATUS: Score {overall_score}/10. Action: {recommended_action}.\n"
    if recommended_action == "continue_discovery" or overall_score < 7:
        instruction += "INSTRUCTION: Do NOT suggest a call yet. Keep discovering. You need more information.\n"
    elif overall_score >= 7:
        instruction += "INSTRUCTION: Lead is qualified. Suggest a call with Louis when the moment feels natural.\n"
    
    # Inject form context if present
    if lead_data.get("industry") or lead_data.get("message"):
        instruction += f"\nFORM DATA SUBMITTED: Industry: {lead_data.get('industry')}, Message: {lead_data.get('message')}. Use this to skip basic questions.\n"

    # Append instruction to the system message
    if messages and messages[0]["role"] == "system":
        messages[0]["content"] += instruction
        
    return messages
