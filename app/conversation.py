import asyncio
import logging
from app.config import settings
from app.llm import llm_client
from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.messagebird_client import send_message, send_chunked_messages, reply_to_conversation, reply_chunked_messages, send_typing_indicator
from app.chunker import chunk_message, calculate_typing_delay
from app.state_machine import check_transition
from app.bant import extract_bant
from app.models import ConversationState
from typing import Dict, Any

from app.tracker import AlbertTracker

logger = logging.getLogger(__name__)
tracker = AlbertTracker()

async def process_conversation(phone: str, message: str, conversation_id: str = "", source: str = "text"):
    """Main conversation engine logic."""
    try:
        print(f"\n[Conversation] 🚀 Starting process for {phone} (via {source}): '{message[:100]}...'", flush=True)

        # 2. Get session and lead data
        session = await redis_client.get_session(phone)
        if not session:
            print(f"[Conversation] No session for {phone}, looking up lead...", flush=True)
            lead = tracker.get_lead_by_phone(phone)
            if not lead:
                print(f"[Conversation] Creating new lead for {phone}", flush=True)
                lead = tracker.create_lead(phone=phone)
                
            session = {
                "state": ConversationState.OPENING,
                "history": [],
                "turn_count": 0,
                "lead_data": lead or {"phone": phone}
            }
            print(f"[Conversation] Created fresh session for {phone}", flush=True)
        else:
            print(f"[Conversation] Found session for {phone}, state: {session.get('state')}", flush=True)
        
        lead_data = session.get("lead_data", {})
        lead_id = lead_data.get("id")

        # 3. Simulate thinking
        print(f"[Conversation] Simulation: Thinking for {phone}...", flush=True)
        await send_typing_indicator(phone, conversation_id)
        await asyncio.sleep(2.0)

        # 4. LLM Call
        print(f"[Conversation] Calling LLM for {phone} using model: {settings.OPENROUTER_PRIMARY_MODEL}", flush=True)
        messages = await llm_client.build_context(session, lead_data, message)
        response_text = await llm_client.call_llm(
            messages,
            model=settings.OPENROUTER_PRIMARY_MODEL,
            lead_id=lead_id,
            conversation_state=session["state"],
            phone=phone,
            company=lead_data.get("company", "")
        )
        
        if not response_text:
            print(f"[Conversation] ❌ LLM returned empty response for {phone}", flush=True)
            return

        print(f"[Conversation] ✅ AI Response received for {phone}", flush=True)

        # 5. Chunk and send response
        chunks = chunk_message(response_text)
        if chunks:
            # Human-like typing delay for first chunk
            initial_delay = calculate_typing_delay(chunks[0][:100])
            print(f"[Conversation] Waiting human typing delay: {initial_delay}s", flush=True)
            await asyncio.sleep(initial_delay)

            if len(chunks) == 1:
                print(f"[Conversation] Sending single message to {phone}", flush=True)
                await send_message(phone, chunks[0])
            else:
                print(f"[Conversation] Sending {len(chunks)} chunked messages to {phone}", flush=True)
                await send_chunked_messages(phone, chunks, conversation_id)
            
            print(f"[Conversation] ✅ Message(s) sent to {phone}", flush=True)

        # 6. Update session history
        session["history"].append({"role": "user", "content": message})
        session["history"].append({"role": "assistant", "content": response_text})
        session["history"] = session["history"][-10:]
        session["turn_count"] += 1

        # 7. Tracking outbound
        for chunk in chunks:
            tracker.log_outbound(lead_id, chunk)

        # 8. Check for state transition
        new_state = check_transition(session["state"], session)
        if new_state and new_state != session["state"]:
            print(f"[Conversation] Transitioning state: {session['state']} -> {new_state}", flush=True)
            session["state"] = new_state
            
            state_map = {
                ConversationState.OPENING: "Opening",
                ConversationState.DISCOVERY: "Discovery",
                ConversationState.QUALIFICATION: "Qualification",
                ConversationState.BOOKING: "Booking Push",
                ConversationState.ESCALATION: "Escalation",
                ConversationState.CONFIRMED: "Confirmed",
                ConversationState.CLOSED: "In Progress"
            }
            tracker.update_state(lead_id, state_map.get(new_state, "Opening"))

        # 9. Save session
        await redis_client.save_session(phone, session)

        # 10. Background BANT extraction
        asyncio.create_task(extract_bant(phone, session["history"]))

    except Exception as e:
        print(f"[Conversation] 🚨 CRITICAL ERROR processing {phone}: {e}", flush=True)
        import traceback
        traceback.print_exc()
