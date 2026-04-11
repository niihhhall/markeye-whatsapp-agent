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

logger = logging.getLogger(__name__)
tracker = MarkTracker()

from datetime import datetime, timezone
import random
from app.client_manager import client_manager
from app.training_api import compile_training_data
from app.bant import handle_bant_extraction
from app.semantic_cache import semantic_cache

async def process_conversation(phone: str, message: str, conversation_id: str = "", message_id: str = "", last_message_ts: float = 0, client_id: str = None):
    """Main conversation engine logic."""
    try:
        # Load Client Config EARLY
        client_config = None
        if client_id:
            client_config = await client_manager.get_client_by_id(client_id)
            
        print(f"\n[Conversation] 🚀 Starting process for {phone} (Client: {client_id}): '{message[:50]}...'", flush=True)
        logger.info("\n[Conversation] 🚀 Starting process for %s: '%s...'", phone, message[:50])

        # Step 4: Get session and lead data
        session = await redis_client.get_session(phone)
        if not session:
            lead = await tracker.get_lead_by_phone(phone)
            # Try to resolve client_id from lead if not provided
            if lead and not client_id:
                client_id = lead.get("client_id")
                if client_id:
                    client_config = await client_manager.get_client_by_id(client_id)

            if not lead:
                lead = await tracker.create_lead(phone=phone, client_id=client_id)
            
            session = {
                "state": ConversationState.OPENING,
                "history": [],
                "turn_count": 0,
                "lead_data": lead or {"phone": phone, "client_id": client_id},
                "low_content_count": 0
            }
        
        # Ensure lead_data in session has client_id for downstream
        if client_id and "client_id" not in session["lead_data"]:
            session["lead_data"]["client_id"] = client_id
        
        
        # V4: 24h Session Auto-Close (with returning lead bypass)
        last_updated_str = session.get("last_updated")
        is_cmd = str(message).strip().lower().startswith(("/reset", "#reset"))
        
        if not is_cmd and last_updated_str and session.get("state") not in [ConversationState.CONFIRMED, ConversationState.CLOSED]:
            try:
                from datetime import timezone as _tz
                lu_dt = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                lu_aware = lu_dt if lu_dt.tzinfo else lu_dt.replace(tzinfo=_tz.utc)
                idle_seconds = (datetime.now(_tz.utc) - lu_aware).total_seconds()
                if idle_seconds > 86400:  # 24 hours
                    logger.info("[Conversation] %s returning after 24h idle — reopening session.", phone)
                    lead_name = session.get("lead_data", {}).get("first_name", "there")
                    returning_template = f"Hey {lead_name}, Mark here again from Markeye. Glad you came back — what changed?"
                    # Send template as single message
                    await send_message(phone, returning_template)
                    # Re-initialise session
                    new_session = {
                        "state": ConversationState.OPENING,
                        "history": [{"role": "assistant", "content": returning_template}],
                        "turn_count": 1,
                        "lead_data": session.get("lead_data", {}),
                        "low_content_count": 0,
                    }
                    await redis_client.save_session(phone, new_session)
                    await redis_client.clear_generating(phone)
                    return
            except Exception as e:
                logger.warning("[Conversation] 24h auto-close check failed for %s: %s", phone, e)

        lead_data = session.get("lead_data", {})
        lead_id = lead_data.get("id")

        # Handle Simulation Data collection if #reset was called
        if session.get("sim_collecting"):
            from app.outbound import send_initial_outreach
            import re
            logger.info("[Conversation] 🧪 Processing Simulation data from %s: %s", phone, message)
            
            # Robust extraction using Regex (Look for labels in any line)
            name_m = re.search(r'(?:Name|Naam)\s*[–-]\s*([^\n,]+)', message, re.I)
            comp_m = re.search(r'(?:Company|Business|Agency)(?:\s+name)?\s*[–-]\s*([^\n,]+)', message, re.I)
            ind_m = re.search(r'(?:Industry|Field|Sector)\s*[–-]\s*([^\n,]+)', message, re.I)

            # Fallback parsing if no labels are found (comma/newline split)
            if not any([name_m, comp_m, ind_m]):
                parts = [p.strip() for p in re.split(r'[,\n]', message) if p.strip()]
                name = parts[0] if len(parts) > 0 else "there"
                company = parts[1] if len(parts) > 1 else "Horizon Estates"
                industry = parts[2] if len(parts) > 2 else "Real Estate"
            else:
                name = name_m.group(1).strip() if name_m else "there"
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

            # Trigger outreach in background task (Delay bypassed for simulations)
            print(f"[Conversation] 🧪 Triggering interactive outbound flow for {phone} using {company}", flush=True)
            asyncio.create_task(send_initial_outreach(name, phone, company, fake_form))
            
            # 1. Update session state to Discovery IMMEDIATELY to prevent race condition
            # 2. Add marker to history so LLM knows an intro is coming/sent
            session["state"] = ConversationState.DISCOVERY
            session["sim_collecting"] = False
            # We don't add to history here because send_initial_outreach handles it, 
            # but setting state to Discovery stops the "Opening" logic.
            
            await redis_client.save_session(phone, session)
            if lead_id:
                await tracker.update_state(lead_id, "Discovery")
            
            await send_message(phone, "Perfect! I've updated your details. 🚀\n\nStarting outbound demo now... hold tight!")
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
        # 9.0: Semantic Cache Check (V9)
        # Skip LLM if common intent detected in Discovery/Opening state
        cached_response = None
        current_state = session.get("state", ConversationState.OPENING)
        
        if current_state in [ConversationState.OPENING, ConversationState.DISCOVERY]:
            cached_response = await semantic_cache.get_cached(client_id, message)
            
        if cached_response:
            response_text = cached_response
        else:
            messages = await build_enhanced_context(session, lead_data, message, knowledge_context, client_config=client_config)
            response_text = await llm_client.call_llm(
                messages,
                model=settings.PRIMARY_MODEL,
                lead_id=lead_id,
                conversation_state=session["state"],
                phone=phone,
                company=lead_data.get("company", ""),
                client_config=client_config
            )
            # Update cache if applicable
            if current_state in [ConversationState.OPENING, ConversationState.DISCOVERY]:
                await semantic_cache.set_cache(client_id, message, response_text)

        print(f"[Conversation] 🤖 Response generated for {phone}", flush=True)
        
        if not response_text or "[NO_REPLY]" in response_text.upper():
            if response_text and "[NO_REPLY]" in response_text.upper():
                logger.info("[Conversation] LLM generated [NO_REPLY] for %s. Ignoring and doing nothing.", phone)
            await redis_client.clear_generating(phone)
            return

        # Step 9.5: Trigger Tag Scanner & Execution (V10)
        # Scan for: [SEND_BOOKING_POLL], [SEND_CALENDLY], [SEND_PRICING], [ESCALATE]
        trigger_patterns = {
            "SEND_BOOKING_POLL": r'\[SEND_BOOKING_POLL\]',
            "SEND_CALENDLY": r'\[SEND_CALENDLY\]',
            "SEND_PRICING": r'\[SEND_PRICING\]',
            "ESCALATE": r'\[ESCALATE\]'
        }
        
        fired_triggers = []
        for tag, pattern in trigger_patterns.items():
            if re.search(pattern, response_text, re.I):
                fired_triggers.append(tag)
        
        # Clean response (Strip ALL tags)
        response_text = re.sub(r'\[[A-Z\s_]+:?.*?\]', '', response_text).strip()
        
        if fired_triggers:
            logger.info("[Conversation] 🎯 Fired triggers for %s: %s", phone, fired_triggers)
            # Execute actions in background so they don't delay the main reply
            for trigger in fired_triggers:
                if trigger == "SEND_BOOKING_POLL":
                    asyncio.create_task(send_poll(
                        to=phone, 
                        question="Want to book a quick 15-min discovery call? Pick what works:", 
                        options=["Today", "Tomorrow", "This Week", "Not Yet"]
                    ))
                elif trigger == "SEND_CALENDLY":
                    # Baileys handler will catch the URL and add preview automatically
                    pass 
                elif trigger == "SEND_PRICING":
                    asyncio.create_task(send_media(
                        to=phone, 
                        media_type="document", 
                        url=settings.PRICING_PDF_URL, 
                        caption="Markeye Pricing Overview"
                    ))
                elif trigger == "ESCALATE":
                    if settings.SALES_PHONE_NUMBER:
                        asyncio.create_task(forward_message(
                            to=phone, 
                            original_msg_id=message_id, 
                            forward_to=settings.SALES_PHONE_NUMBER
                        ))

        # Step 9.6: Interrupt Check — did new messages arrive during LLM call?
        new_messages_str = await redis_client.get_and_clear_buffer(phone)
        if new_messages_str:
            logger.info("[Conversation] New messages arrived during processing for %s, re-generating", phone)
            combined = message + "\n" + new_messages_str
            await redis_client.clear_generating(phone)
            # Re-process with combined input
            return await process_conversation(phone, combined, conversation_id, message_id, client_id=client_id)

        # Step 10: Calendly Resend Logic (Fix 3)
        response_text = await check_and_send_calendly(phone, response_text, session, client_config=client_config)

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
        # Fetch metadata from Redis cached in llm.py
        llm_metadata = {}
        try:
            cached = await redis_client.redis.get(f"last_llm_usage:{phone}")
            if cached:
                llm_metadata = json.loads(cached)
                await redis_client.redis.delete(f"last_llm_usage:{phone}")
        except:
            pass
            
        await tracker.log_outbound(lead_id, response_text, client_id=client_id, metadata=llm_metadata)

        # Step 14: Check for state transition
        new_state = check_transition(session["state"], session, client_config=client_config)
        
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

        # Background Smart Extraction (V9)
        asyncio.create_task(handle_bant_extraction(phone, message, session["history"], client_config=client_config))

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
