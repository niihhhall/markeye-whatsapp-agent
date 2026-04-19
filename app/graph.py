"""
LangGraph StateGraph — Markeye Conversation Orchestration Engine.

Implements the Whatsapp-Langgraph-Agent-Integration pattern:
  Each node is an isolated async handler for one stage of the conversation.
  Conditional edges route based on state flags, replacing the monolithic
  process_conversation() imperative flow.

Node chain:
  load_context → [handle_special | classify_stage]
  classify_stage → check_spam → retrieve_knowledge → generate_response
  generate_response → execute_tools → deliver_response → persist_session → END
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import TypedDict, Optional, List, Any

from langgraph.graph import StateGraph, END

from app.models import ConversationState
from app.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Graph State Schema
# ─────────────────────────────────────────────────────────────────────────────

class GraphState(TypedDict):
    # ── Input params ─────────────────────────────────────
    phone: str
    message: str
    conversation_id: str
    message_id: str
    last_message_ts: float
    client_id: Optional[str]

    # ── Loaded context ────────────────────────────────────
    session: dict
    lead_data: dict
    lead_id: Optional[str]
    client_config: Optional[dict]

    # ── Processing ────────────────────────────────────────
    knowledge_context: str
    response_text: str
    tool_calls: List[str]

    # ── Control flow ──────────────────────────────────────
    should_exit: bool
    exit_reason: str


# ─────────────────────────────────────────────────────────────────────────────
# NODE 1 — Load context
# ─────────────────────────────────────────────────────────────────────────────

async def load_context(state: GraphState) -> dict:
    """Load session, lead data, and client config. Handle 24h returning lead."""
    from app.redis_client import redis_client
    from app.tracker import MarkTracker
    from app.client_manager import client_manager
    from app.message_router import send_message

    phone = state["phone"]
    message = state["message"]
    client_id = state.get("client_id")
    tracker = MarkTracker()

    # Load client config
    client_config = None
    if client_id:
        client_config = await client_manager.get_client_by_id(client_id)

    # Get or create session
    session = await redis_client.get_session(phone)
    if not session:
        lead = await tracker.get_lead_by_phone(phone)
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
            "low_content_count": 0,
        }

    if client_id and "client_id" not in session.get("lead_data", {}):
        session["lead_data"]["client_id"] = client_id

    lead_data = session.get("lead_data", {})
    lead_id = lead_data.get("id")

    # 24h returning lead check
    last_updated_str = session.get("last_updated")
    is_cmd = str(message).strip().lower().startswith(("/reset", "#reset"))

    if not is_cmd and last_updated_str and session.get("state") not in [ConversationState.CONFIRMED]:
        try:
            from datetime import timezone as _tz
            lu_dt = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
            lu_aware = lu_dt if lu_dt.tzinfo else lu_dt.replace(tzinfo=_tz.utc)
            idle_seconds = (datetime.now(_tz.utc) - lu_aware).total_seconds()

            if idle_seconds > 86400:
                logger.info("[Graph] %s returning after 24h idle — reopening session.", phone)
                lead_name = lead_data.get("first_name", "there")
                returning_msg = (
                    f"Hey {lead_name}, Mark here again from Markeye. "
                    f"Glad you came back — what changed?"
                )
                await send_message(phone, returning_msg, client_config=client_config)
                new_session = {
                    "state": ConversationState.OPENING,
                    "history": [{"role": "assistant", "content": returning_msg}],
                    "turn_count": 1,
                    "lead_data": lead_data,
                    "low_content_count": 0,
                }
                await redis_client.save_session(phone, new_session)
                await redis_client.clear_generating(phone)
                return {
                    "should_exit": True, "exit_reason": "returning_lead_handled",
                    "session": new_session, "lead_data": lead_data,
                    "lead_id": lead_id, "client_config": client_config,
                    "client_id": client_id, "knowledge_context": "",
                    "response_text": "", "tool_calls": [],
                }
        except Exception as e:
            logger.warning("[Graph] 24h check failed for %s: %s", phone, e)

    return {
        "session": session,
        "lead_data": lead_data,
        "lead_id": lead_id,
        "client_id": client_id,
        "client_config": client_config,
        "should_exit": False,
        "exit_reason": "",
        "knowledge_context": "",
        "response_text": "",
        "tool_calls": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODE 2 — Handle special commands (/reset, #reset, sim_collecting)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_special(state: GraphState) -> dict:
    """Handle /reset, #reset commands and interactive simulation mode."""
    from app.redis_client import redis_client
    from app.tracker import MarkTracker
    from app.message_router import send_message
    from app.supabase_client import supabase_client

    phone = state["phone"]
    message = state["message"]
    session = state["session"]
    lead_data = state["lead_data"]
    client_config = state.get("client_config")
    tracker = MarkTracker()

    # Simulation data collection
    if session.get("sim_collecting"):
        from app.outbound import send_initial_outreach
        name_m = re.search(r'(?:Name|Naam)\s*[–-]\s*([^\n,]+)', message, re.I)
        comp_m = re.search(r'(?:Company|Business|Agency)(?:\s+name)?\s*[–-]\s*([^\n,]+)', message, re.I)
        ind_m = re.search(r'(?:Industry|Field|Sector)\s*[–-]\s*([^\n,]+)', message, re.I)

        if not any([name_m, comp_m, ind_m]):
            parts = [p.strip() for p in re.split(r'[,\n]', message) if p.strip()]
            name = parts[0] if parts else "there"
            company = parts[1] if len(parts) > 1 else "Horizon Estates"
            industry = parts[2] if len(parts) > 2 else "Real Estate"
        else:
            name = name_m.group(1).strip() if name_m else "there"
            company = comp_m.group(1).strip() if comp_m else "Horizon Estates"
            industry = ind_m.group(1).strip() if ind_m else "Real Estate"

        fake_form = {
            "first_name": name, "company": company, "industry": industry,
            "role": "Director",
            "message": f"I want to automate my {industry} agency discovery calls.",
            "source": "Interactive Reset Simulation",
        }

        if lead_id:
            client = await supabase_client.get_client()
            await client.table("leads").update({
                "first_name": name, "company": company, "industry": industry,
                "form_message": fake_form["message"],
            }).eq("id", lead_id).execute()

        asyncio.create_task(send_initial_outreach(name, phone, company, fake_form))
        session["state"] = ConversationState.DISCOVERY
        session["sim_collecting"] = False
        await redis_client.save_session(phone, session)
        if lead_id:
            await tracker.update_state(lead_id, "Discovery")
        await send_message(phone, "Perfect! I've updated your details. 🚀\n\nStarting outbound demo now... hold tight!", client_config=client_config)
        await redis_client.clear_generating(phone)
        return {"should_exit": True, "exit_reason": "sim_handled", "session": session}

    # /reset or #reset
    raw_cmd = message.strip().lower()
    if raw_cmd.startswith("/reset") or raw_cmd.startswith("#reset"):
        cmd = "#reset" if "#reset" in raw_cmd else "/reset"
        new_session = {
            "state": ConversationState.OPENING, "history": [], "turn_count": 0,
            "lead_data": lead_data, "low_content_count": 0,
            "sim_collecting": (cmd == "#reset"),
        }
        await redis_client.save_session(phone, new_session)
        if lead_id:
            await tracker.update_state(lead_id, "Opening")

        if cmd == "#reset":
            await send_message(phone, "🚀 #reset: Simulation started! Let's get your details.\n\nType your **Name, Company Name, Industry** (e.g. Nihal, Horizon Estates, Real Estate)", client_config=client_config)
        else:
            await send_message(phone, "I've reset the conversation for you. Please clear the chat on your end and start a new one whenever you're ready.\n\n(Tip: Use **#reset** if you want to start a full website form simulation!)", client_config=client_config)

        await redis_client.clear_generating(phone)
        return {"should_exit": True, "exit_reason": "reset_handled", "session": new_session}

    return {"should_exit": False}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 3 — LLM Stage Classifier  (SalesGPT dual-chain pattern)
# ─────────────────────────────────────────────────────────────────────────────

async def classify_stage_node(state: GraphState) -> dict:
    """
    Runs LLM stage classifier concurrently with rule-based transition check.
    LLM result takes precedence; rule-based is fallback.
    3-second timeout so this never blocks the response.
    """
    from app.state_machine import check_transition, classify_stage_with_llm

    session = state["session"]
    current_state = session.get("state", ConversationState.OPENING)
    client_config = state.get("client_config")

    # LLM classifier (async, with timeout)
    try:
        llm_stage = await asyncio.wait_for(
            classify_stage_with_llm(session.get("history", []), current_state),
            timeout=3.0,
        )
        if llm_stage and llm_stage != current_state:
            logger.info("[Graph] LLM reclassified %s → %s for %s", current_state, llm_stage, state["phone"])
            session["state"] = llm_stage
    except asyncio.TimeoutError:
        logger.warning("[Graph] Stage classifier timed out for %s, keeping current state.", state["phone"])
    except Exception as e:
        logger.warning("[Graph] Stage classifier error for %s: %s", state["phone"], e)

    # Rule-based transition (always run as secondary check)
    new_state = check_transition(session["state"], session, client_config=client_config)
    if new_state and new_state != session["state"]:
        logger.info("[Graph] Rule-based transition: %s → %s", session["state"], new_state)
        session["state"] = new_state

    return {"session": session}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 4 — Spam / low-content check
# ─────────────────────────────────────────────────────────────────────────────

async def check_spam_node(state: GraphState) -> dict:
    """Filter low-content spam. Never applies in OPENING state."""
    from app.redis_client import redis_client
    from app.message_router import send_message

    phone = state["phone"]
    message = state["message"]
    session = state["session"]
    client_config = state.get("client_config")

    if session.get("state") == ConversationState.OPENING:
        return {"should_exit": False}

    content = message.strip().lower().rstrip("!?.")
    words = content.split()
    low_content_patterns = ["hey","heyy","heyyy","hi","hello","yo","sup","?","ok","k","yeah","nice"]
    is_low_content = (len(words) < 2 and content in low_content_patterns) or len(words) == 0

    if is_low_content:
        count = session.get("low_content_count", 0) + 1
        session["low_content_count"] = count

        if count == 2:
            await send_message(phone, "Haha what's up, you good?", client_config=client_config)
            await redis_client.save_session(phone, session)
            await redis_client.clear_generating(phone)
            return {"should_exit": True, "exit_reason": "low_content_tier1", "session": session}

        if count >= 3:
            session["state"] = ConversationState.WAITING
            await redis_client.save_session(phone, session)
            await send_message(phone, "Hey, timing might be off. I'm here whenever you want to have a proper chat.", client_config=client_config)
            await redis_client.clear_generating(phone)
            return {"should_exit": True, "exit_reason": "waiting_state", "session": session}
    else:
        session["low_content_count"] = 0

    return {"should_exit": False, "session": session}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 5 — Knowledge retrieval (RAG + semantic cache)
# ─────────────────────────────────────────────────────────────────────────────

async def retrieve_knowledge_node(state: GraphState) -> dict:
    """RAG retrieval + semantic cache check before LLM call."""
    from app.knowledge import retrieve_knowledge
    from app.semantic_cache import semantic_cache

    phone = state["phone"]
    message = state["message"]
    session = state["session"]
    current_state = session.get("state", ConversationState.OPENING)
    client_id = state.get("client_id")

    # Semantic cache check
    if current_state in [ConversationState.OPENING, ConversationState.DISCOVERY]:
        cached = await semantic_cache.get_cached(client_id, message)
        if cached:
            logger.info("[Graph] Cache hit for %s", phone)
            return {"response_text": cached, "knowledge_context": ""}

    knowledge_context = await retrieve_knowledge(message)
    return {"knowledge_context": knowledge_context or ""}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 6 — Generate response + classify tools
# ─────────────────────────────────────────────────────────────────────────────

async def generate_response_node(state: GraphState) -> dict:
    """Main LLM generation + separate tool classification (Knotie-AI + llmstatemachine pattern)."""
    from app.llm import llm_client
    from app.agent_tools import classify_tools
    from app.semantic_cache import semantic_cache
    from app.message_router import send_typing_indicator
    from app.tracker import MarkTracker
    from app.redis_client import redis_client

    phone = state["phone"]
    message = state["message"]
    session = state["session"]
    lead_data = state["lead_data"]
    lead_id = state.get("lead_id")
    client_config = state.get("client_config")
    client_id = state.get("client_id")
    knowledge_context = state.get("knowledge_context", "")
    tracker = MarkTracker()

    # If retrieve_knowledge found a cached response, just classify tools for it
    if state.get("response_text"):
        tool_calls = await classify_tools(session, message, state["response_text"])
        return {"tool_calls": tool_calls}

    # Typing indicator before LLM call
        client_config=client_config
    )
    if lead_id:
        await tracker.set_typing_status(lead_id, True)

    # Set processing flag
    await redis_client.set_generating(phone)

    # Build context and call LLM
    from app.graph_utils import build_enhanced_context
    messages = await build_enhanced_context(
        session, lead_data, message, knowledge_context, client_config=client_config
    )

    response_text = await llm_client.call_llm(
        messages,
        model=settings.PRIMARY_MODEL,
        lead_id=lead_id,
        conversation_state=session["state"],
        phone=phone,
        company=lead_data.get("company", ""),
        client_config=client_config,
    )

    if not response_text or "[NO_REPLY]" in response_text.upper():
        await redis_client.clear_generating(phone)
        return {"should_exit": True, "exit_reason": "no_reply", "response_text": ""}

    # Cache if applicable
    current_state = session.get("state", ConversationState.OPENING)
    if current_state in [ConversationState.OPENING, ConversationState.DISCOVERY]:
        await semantic_cache.set_cache(client_id, message, response_text)

    # Clean any remaining legacy bracket tokens from response
    response_text = re.sub(r'(?i)\[[A-Z0-9\s_]+:?.*?\]', '', response_text).strip()

    # Classify which tools to fire (per-state whitelist enforced inside classify_tools)
    tool_calls = await classify_tools(session, message, response_text)

    return {"response_text": response_text, "tool_calls": tool_calls, "should_exit": False}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 7 — Execute tool calls
# ─────────────────────────────────────────────────────────────────────────────

async def execute_tools_node(state: GraphState) -> dict:
    """Fire any tools the classifier decided on."""
    from app.agent_tools import execute_tool_call

    tool_calls = state.get("tool_calls", [])
    if not tool_calls:
        return {}

    phone = state["phone"]
    message_id = state.get("message_id", "")
    session = state["session"]
    client_config = state.get("client_config")

    logger.info("[Graph] Executing tools for %s: %s", phone, tool_calls)

    for tool_name in tool_calls:
        try:
            await execute_tool_call(tool_name, phone, message_id, session, client_config)
        except Exception as e:
            logger.error("[Graph] Tool '%s' failed for %s: %s", tool_name, phone, e)

    return {"session": session}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 8 — Deliver response
# ─────────────────────────────────────────────────────────────────────────────

async def deliver_response_node(state: GraphState) -> dict:
    """Send multi-bubble response to lead. Handles interrupt check."""
    from app.message_router import send_chunked_messages
    from app.chunker import chunk_message
    from app.tracker import MarkTracker
    from app.redis_client import redis_client
    from app.graph_utils import check_and_send_calendly

    phone = state["phone"]
    response_text = state.get("response_text", "")
    message = state["message"]
    lead_id = state.get("lead_id")
    client_config = state.get("client_config")
    tracker = MarkTracker()

    if not response_text:
        return {}

    # Interrupt check — new messages during LLM processing
    new_messages_str = await redis_client.get_and_clear_buffer(phone)
    if new_messages_str:
        logger.info("[Graph] Interrupt detected for %s — re-invoking with combined input.", phone)
        combined_message = message + "\n" + new_messages_str
        await redis_client.clear_generating(phone)
        new_state = {
            **state,
            "message": combined_message,
            "response_text": "",
            "tool_calls": [],
            "should_exit": False,
            "exit_reason": "",
        }
        await workflow.ainvoke(new_state)
        return {"should_exit": True, "exit_reason": "re_invoked"}

    # Calendly tracking
    response_text = await check_and_send_calendly(
        phone, response_text, state["session"], client_config=client_config
    )

    # Send
    chunks = chunk_message(response_text)
        client_config=client_config
    )

    if lead_id:
        await tracker.set_typing_status(lead_id, False)

    return {"response_text": response_text}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 9 — Persist session
# ─────────────────────────────────────────────────────────────────────────────

async def persist_session_node(state: GraphState) -> dict:
    """Update history, run BANT extraction, save session, trigger background tasks."""
    from app.redis_client import redis_client
    from app.tracker import MarkTracker
    from app.bant import handle_bant_extraction
    from app.graph_utils import on_conversation_end

    phone = state["phone"]
    session = state["session"]
    message = state["message"]
    response_text = state.get("response_text", "")
    lead_id = state.get("lead_id")
    client_id = state.get("client_id")
    client_config = state.get("client_config")
    tracker = MarkTracker()

    # Update history
    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": response_text})
    session["history"] = session["history"][-50:]
    session["turn_count"] = session.get("turn_count", 0) + 1
    session["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Exit phrase detection → CLOSED
    exit_phrases = [
        "no worries, you know where to find us",
        "come back when you want to chat properly",
        "all the best",
        "leave it there",
    ]
    if response_text and any(p in response_text.lower() for p in exit_phrases):
        session["state"] = ConversationState.CLOSED

    # Log outbound
    llm_metadata = {}
    try:
        cached = await redis_client.redis.get(f"last_llm_usage:{phone}")
        if cached:
            llm_metadata = json.loads(cached)
            await redis_client.redis.delete(f"last_llm_usage:{phone}")
    except Exception:
        pass

    await tracker.log_outbound(lead_id, response_text, client_id=client_id, metadata=llm_metadata)

    # Sync state to tracker
    state_map = {
        ConversationState.OPENING:       "Opening",
        ConversationState.DISCOVERY:     "Discovery",
        ConversationState.QUALIFICATION: "Qualification",
        ConversationState.BOOKING:       "Booking Push",
        ConversationState.ESCALATION:    "Escalation",
        ConversationState.CONFIRMED:     "Confirmed",
        ConversationState.WAITING:       "Waiting",
        ConversationState.CLOSED:        "Closed",
    }
    if lead_id:
        await tracker.update_state(lead_id, state_map.get(session.get("state"), "Opening"))

    # Persist
    await redis_client.save_session(phone, session)
    await redis_client.clear_generating(phone)

    # Background: closed conversation scoring
    if session.get("state") == ConversationState.CLOSED:
        asyncio.create_task(on_conversation_end(phone, "exit_clean", session, lead_id))

    # Background: BANT extraction
    asyncio.create_task(
        handle_bant_extraction(phone, message, session["history"], client_config=client_config)
    )

    return {"session": session}


# ─────────────────────────────────────────────────────────────────────────────
# Conditional edge routing functions
# ─────────────────────────────────────────────────────────────────────────────

def route_after_load(state: GraphState) -> str:
    if state.get("should_exit"):
        return END
    msg = state.get("message", "").strip().lower()
    sess = state.get("session", {})
    if msg.startswith(("/reset", "#reset")) or sess.get("sim_collecting"):
        return "handle_special"
    return "classify_stage"


def route_after_special(state: GraphState) -> str:
    return END if state.get("should_exit") else "classify_stage"


def route_after_spam(state: GraphState) -> str:
    return END if state.get("should_exit") else "retrieve_knowledge"


def route_after_generate(state: GraphState) -> str:
    return END if state.get("should_exit") else "execute_tools"


def route_after_deliver(state: GraphState) -> str:
    return END if state.get("should_exit") else "persist_session"


# ─────────────────────────────────────────────────────────────────────────────
# Build and compile the graph
# ─────────────────────────────────────────────────────────────────────────────

def _build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("load_context",       load_context)
    graph.add_node("handle_special",     handle_special)
    graph.add_node("classify_stage",     classify_stage_node)
    graph.add_node("check_spam",         check_spam_node)
    graph.add_node("retrieve_knowledge", retrieve_knowledge_node)
    graph.add_node("generate_response",  generate_response_node)
    graph.add_node("execute_tools",      execute_tools_node)
    graph.add_node("deliver_response",   deliver_response_node)
    graph.add_node("persist_session",    persist_session_node)

    graph.set_entry_point("load_context")

    graph.add_conditional_edges(
        "load_context", route_after_load,
        {END: END, "handle_special": "handle_special", "classify_stage": "classify_stage"},
    )
    graph.add_conditional_edges(
        "handle_special", route_after_special,
        {END: END, "classify_stage": "classify_stage"},
    )
    graph.add_edge("classify_stage",     "check_spam")
    graph.add_conditional_edges(
        "check_spam", route_after_spam,
        {END: END, "retrieve_knowledge": "retrieve_knowledge"},
    )
    graph.add_edge("retrieve_knowledge", "generate_response")
    graph.add_conditional_edges(
        "generate_response", route_after_generate,
        {END: END, "execute_tools": "execute_tools"},
    )
    graph.add_edge("execute_tools",      "deliver_response")
    graph.add_conditional_edges(
        "deliver_response", route_after_deliver,
        {END: END, "persist_session": "persist_session"},
    )
    graph.add_edge("persist_session", END)

    return graph.compile()


# Compiled workflow — imported by conversation.py
workflow = _build_graph()
