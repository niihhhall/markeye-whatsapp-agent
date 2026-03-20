import asyncio
import logging
from app.config import settings
from app.llm import llm_client
from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.models import ConversationState
from app.messaging import send_message, send_chunked_messages, send_typing_indicator, mark_as_read
from app.chunker import chunk_message, calculate_typing_delay
from app.state_machine import check_transition
from app.bant import extract_bant
from app.knowledge import retrieve_knowledge
from typing import Dict, Any

from app.tracker import AlbertTracker

logger = logging.getLogger(__name__)
tracker = AlbertTracker()

from datetime import datetime, timezone
import random
from app.signals import detect_interest_level, detect_personality_type, get_approach_instructions

async def process_conversation(phone: str, message: str, conversation_id: str = "", message_id: str = "", last_message_ts: float = 0):
    """Main conversation engine logic."""
    try:
        print(f"\n[Conversation] 🚀 Starting process for {phone}: '{message[:50]}...'", flush=True)
        logger.info("\n[Conversation] 🚀 Starting process for %s: '%s...'", phone, message[:50])

        # Step 4: Get session and lead data
        session = await redis_client.get_session(phone)
        if not session:
            lead = await tracker.get_lead_by_phone(phone)
            if not lead:
                lead = await tracker.create_lead(phone=phone)
            session = {
                "state": ConversationState.OPENING,
                "history": [],
                "turn_count": 0,
                "lead_data": lead or {"phone": phone},
                "low_content_count": 0
            }
        
        lead_data = session.get("lead_data", {})
        lead_id = lead_data.get("id")

        # Handle Simulation Data collection if #reset was called
        if session.get("sim_collecting"):
            from app.outbound import send_initial_outreach
            import re
            logger.info("[Conversation] 🧪 Processing Simulation data from %s: %s", phone, message)
            
            # Robust extraction using Regex (Look for start of lines or labels)
            name_m = re.search(r'Name\s*[–-]\s*([^\n,]+)', message, re.I)
            comp_m = re.search(r'Company(?:\s+name)?\s*[–-]\s*([^\n,]+)', message, re.I)
            ind_m = re.search(r'Industry\s*[–-]\s*([^\n,]+)', message, re.I)

            name = name_m.group(1).strip() if name_m else "Nihal"
            company = comp_m.group(1).strip() if comp_m else "Horizon Estates"
            industry = ind_m.group(1).strip() if ind_m else "Real Estate"

            fake_form = {
                "first_name": name,
                "company": company,
                "industry": industry,
                "role": "Director",
                "message": f"I want to automate my {industry} agency discovery calls.",
                "source": "Interactive Reset Simulation"
            }
            
            # Update lead in Supabase with these provided details
            if lead_id:
                client = await supabase_client.get_client()
                await client.table("leads").update({
                    "first_name": name,
                    "company": company,
                    "industry": industry,
                    "form_message": fake_form["message"]
                }).eq("id", lead_id).execute()

            # Trigger outreach in background task (15s delay inside)
            print(f"[Conversation] 🧪 Triggering interactive outbound flow for {phone} using {company}", flush=True)
            asyncio.create_task(send_initial_outreach(name, phone, company, fake_form))
            
            # Clear sim flag
            session["sim_collecting"] = False
            await redis_client.save_session(phone, session)
            
            await send_message(phone, "Perfect! I've updated your details. 🚀\n\nOutbound simulation starting in 15 seconds. Hold tight!")
            await redis_client.clear_generating(phone)
            return

        # Step 3: Handle /reset and #reset commands
        raw_cmd = message.strip().lower()
        if raw_cmd.startswith("/reset") or raw_cmd.startswith("#reset"):
            cmd = "#reset" if "#reset" in raw_cmd else "/reset"
            logger.info("[Conversation] Reset command detected for %s. Clearing session.", phone)
            
            # 1. Clear Redis session
            session = {
                "state": ConversationState.OPENING,
                "history": [],
                "turn_count": 0,
                "lead_data": lead_data, # Reuse existing lead_data (phone, name etc)
                "low_content_count": 0,
                "sim_collecting": (cmd == "#reset") # Flag for interactive simulation
            }
            await redis_client.save_session(phone, session)
            
            # 2. Sync with Supabase (Critical for live checks)
            if lead_id:
                await tracker.update_state(lead_id, "Opening")

            # 3. Handle specific command replies
            if cmd == "#reset":
                await send_message(phone, "🚀 #reset: Simulation started! Let's get your details.\n\nType your **Name, Company Name, Industry** (e.g. Nihal, Horizon Estates, Real Estate)")
            else:
                await send_message(phone, "I've reset the conversation for you. Please clear the chat on your end and start a new one whenever you're ready.\n\n(Tip: Use **#reset** if you want to start a full website form simulation!)")
            
            await redis_client.clear_generating(phone)
            return

        # ... (Step 4 is moved up, so we'll just skip it below) ...

        # Step 5: Start Typing Indicator (Simulates "Writing...")
        # We start this BEFORE LLM call so the user knows we are responding
        print(f"[Conversation] ✍️ Starting typing simulation for {phone}", flush=True)
        await send_typing_indicator(phone, conversation_id, message_id)
        if lead_id:
            await tracker.set_typing_status(lead_id, True)

        # Step 6: Set processing flag
        await redis_client.set_generating(phone)

        # Step 7: Check if message is low-content spam
        is_spam = await check_low_content(phone, message, session)
        if is_spam:
            return

        # Step 8: Knowledge Base Retrieval (RAG)
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
            await redis_client.clear_generating(phone)
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
            await redis_client.clear_generating(phone)
            # Re-process with combined input
            return await process_conversation(phone, combined, conversation_id, message_id)

        # Step 10: Calendly Resend Logic (Fix 3)
        response_text = await check_and_send_calendly(phone, response_text, session)

        # Step 11: Send natural multi-bubble response
        if response_text:
            print(f"[Conversation] 📤 Splitting and sending multi-bubble response to {phone}", flush=True)
            chunks = chunk_message(response_text)
            
            # Use the existing utility that handles delays and typing indicators
            await send_chunked_messages(
                to=phone, 
                chunks=chunks, 
                incoming_text=message, 
                last_message_ts=last_message_ts, 
                message_id=message_id
            )
            
            if lead_id:
                await tracker.set_typing_status(lead_id, False)

        # Step 12: Update session history and turn count
        session["history"].append({"role": "user", "content": message})
        session["history"].append({"role": "assistant", "content": response_text})
        session["history"] = session["history"][-50:]
        session["turn_count"] += 1
        session["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Step 13: Tracking outbound
        await tracker.log_outbound(lead_id, response_text)

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
            await tracker.update_state(lead_id, state_map.get(new_state, "Opening"))

        # Step 15: Cleanup and background tasks
        await redis_client.save_session(phone, session)
        await redis_client.clear_generating(phone)
        
        # Phase 3: Auto-scoring on termination
        if new_state == ConversationState.CLOSED:
            # Determine outcome based on context if not explicit
            outcome = "exit_clean"
            if any(phrase in response_lower for phrase in ["no worries", "all the best"]):
                outcome = "exit_clean"
            # In a real scenario, we'd check if they booked (CONFIRMED state)
            asyncio.create_task(on_conversation_end(phone, outcome, session, lead_id))

        asyncio.create_task(extract_bant(phone, session["history"]))

    except Exception as e:
        logger.critical("[Conversation] 🚨 CRITICAL ERROR processing %s: %s", phone, e, exc_info=True)
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


async def check_and_send_calendly(phone: str, text: str, session: dict) -> str:
    """
    Tracks if Calendly link was sent.
    We no longer block resending it if the user explicitly asks for it again.
    """
    calendly_link = settings.CALENDLY_LINK
    
    if calendly_link in text:
        if not await redis_client.has_sent_calendly(phone):
            await redis_client.mark_calendly_sent(phone)
            logger.info("[Conversation] Tracking Calendly link sent to %s", phone)

    return text


async def build_enhanced_context(session: dict, lead_data: dict, message: str, knowledge_context: str = "") -> list:
    """Builds enhanced LLM context with BANT, Form data and Knowledge base context."""
    
    # 0. Live Booking Verification (New)
    # If the user mentions booking, or we are in a booking-related state, 
    # re-fetch the absolute truth from the database to avoid Redis lag or stale cache.
    msg_low = message.lower()
    booking_keywords = ["booked", "done", "scheduled", "appointment", "calendar", "confirm"]
    
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
    messages = await llm_client.build_context(session, lead_data, message, knowledge_context)
    
    # Add the latest_booking_info to the system prompt if found
    if latest_booking_info and messages and messages[0]["role"] == "system":
        messages[0]["content"] += f"\n\n═══ LIVE SYSTEM DATA ═══\n{latest_booking_info}\n"
        if is_new_booking:
            messages[0]["content"] += "IMPORTANT: A new booking was just detected in the system within the last 15 minutes. You MUST acknowledge this.\n"
        else:
            messages[0]["content"] += "IMPORTANT: No new booking found in the last 15 minutes. If the user claims they just booked, they are lying or the system hasn't updated. Tell them to wait a second or try again.\n"

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
        instruction += "INSTRUCTION: Lead is qualified. Suggest a call with Louis when the moment feels natural. Suggest it as a logical next step to solve their problem.\n"
    
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

    # Append everything to the system message
    if messages and messages[0]["role"] == "system":
        if rag_training:
            messages[0]["content"] += f"\n\n--- SALES TRAINING MODULE ---\n{rag_training}\n"
        messages[0]["content"] += instruction
        
    return messages
