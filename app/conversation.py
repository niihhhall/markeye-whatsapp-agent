import asyncio
from app.llm import llm_client
from app.redis_client import redis_client
from app.supabase_client import supabase_client
from app.messagebird_client import send_message, send_chunked_messages, reply_to_conversation, reply_chunked_messages, send_typing_indicator
from app.chunker import chunk_message, calculate_typing_delay
from app.state_machine import check_transition
from app.bant import extract_bant
from app.models import ConversationState
from typing import Dict, Any

async def process_conversation(phone: str, message: str, conversation_id: str = "", source: str = "text"):
    """Main conversation engine logic."""
    print(f"\n[Conversation] Processing {source} message from {phone}: '{message}'")

    # 2. Get session and lead data
    session = await redis_client.get_session(phone)
    if not session:
        # Try to find lead in Supabase
        lead = await supabase_client.get_lead_by_phone(phone)
        session = {
            "state": ConversationState.OPENING,
            "history": [],
            "turn_count": 0,
            "lead_data": lead or {"phone": phone}
        }
    
    lead_data = session.get("lead_data", {})

    # 3. Log inbound message
    await supabase_client.log_message(phone, "inbound", message, session["state"], source=source)

    # 3.1 Show typing and thinking delay
    print(f"[Conversation] Simulation: Human delay + Typing indicator for {phone}")
    await send_typing_indicator(phone)
    # Simulate thinking time (1.5s to 3s)
    await asyncio.sleep(2.0)

    # 4. Build context and call LLM
    print(f"[Conversation] Calling LLM for {phone} using model: {settings.OPENROUTER_PRIMARY_MODEL}")
    messages = await llm_client.build_context(session, lead_data, message)
    response_text = await llm_client.call_llm(
        messages,
        session_id=phone,
        session_path=f"/agent/{session['state']}",
        session_name=f"WhatsApp Chat - {phone}",
        user_id=phone,
        properties={
            "phone": phone,
            "state": session['state'],
            "flow_type": "whatsapp_chat"
        },
        cache_enabled=True
    )
    print(f"[Conversation] AI Response for {phone}: '{response_text}'")

    # 5. Chunk and send response
    chunks = chunk_message(response_text)

    # Add initial typing delay for the first message to feel human
    if chunks:
        initial_delay = calculate_typing_delay(chunks[0][:100])
        await asyncio.sleep(initial_delay)

    if len(chunks) == 1:
        # Single message — prefer reply_to_conversation if we have the ID
        if conversation_id:
            await reply_to_conversation(conversation_id, chunks[0])
        else:
            await send_message(phone, chunks[0])
    else:
        # Multiple chunks — send with typing delays
        if conversation_id:
            await reply_chunked_messages(conversation_id, chunks)
        else:
            await send_chunked_messages(phone, chunks)

    # 6. Update session history
    # Add user message
    session["history"].append({"role": "user", "content": message})
    # Add assistant response
    session["history"].append({"role": "assistant", "content": response_text})
    # Keep last 10
    session["history"] = session["history"][-10:]
    session["turn_count"] += 1

    # 7. Log outbound messages
    for chunk in chunks:
        await supabase_client.log_message(phone, "outbound", chunk, session["state"])

    # 8. Check for state transition
    new_state = check_transition(session["state"], session)
    if new_state and new_state != session["state"]:
        session["state"] = new_state
        await supabase_client.update_lead_status(phone, f"state_{new_state}")

    # 9. Save session
    await redis_client.save_session(phone, session)

    # 10. Background BANT extraction
    asyncio.create_task(extract_bant(phone, session["history"]))
