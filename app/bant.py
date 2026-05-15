import json
import os
import logging
from typing import List, Dict, Any
from app.config import settings
from app.llm import llm_client
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

from app.tracker import MarkTracker

tracker = MarkTracker()

def should_extract_bant(message: str, count: int, current_state: str) -> bool:
    """Heuristic to decide if we should trigger expensive BANT extraction."""
    # 1. Force extract if state just moved to qualification
    if current_state == "qualification":
        return True
        
    # 2. Force extract every 3 messages
    if count >= 3:
        return True
        
    # 3. Detect BANT keywords
    msg_low = message.lower()
    keywords = {
        "budget": ["budget", "spend", "cost", "price", "afford", "invest"],
        "authority": ["decide", "boss", "team", "approve", "ceo", "founder"],
        "need": ["need", "problem", "struggling", "pain", "challenge", "issue"],
        "timeline": ["when", "urgent", "asap", "timeline", "deadline", "soon"]
    }
    for category in keywords:
        if any(kw in msg_low for kw in keywords[category]):
            return True
            
    return False

async def extract_bant(phone: str, history: List[Dict[str, str]], client_config: dict = None):
    """Background BANT extraction using a cheap LLM model."""
    prompt_path = os.path.join(os.getcwd(), "prompts", "bant_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        bant_prompt = f.read()

    # Client-specific criteria injection
    criteria_text = ""
    if client_config and client_config.get("bant_criteria"):
        criteria_text = f"\nCustom Client BANT Criteria: {json.dumps(client_config['bant_criteria'])}\n"
    
    # Custom Questions check
    custom_questions = ""
    if client_config and client_config.get("qualification_questions"):
        questions = client_config.get("qualification_questions")
        if isinstance(questions, list):
            custom_questions = "\nADDITIONAL QUESTIONS TO VERIFY:\n" + "\n".join(f"- {q}" for q in questions)
    
    # Format history for prompt
    history_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history])
    system_prompt = bant_prompt.replace("{{conversation_history}}", history_text)
    
    if criteria_text or custom_questions:
        system_prompt += f"\n\n═══ CLIENT SPECIFIC INSTRUCTIONS ═══{criteria_text}{custom_questions}"

    messages = [{"role": "system", "content": system_prompt}]

    try:
        # Load current session
        session = await redis_client.get_session(phone)
        if not session:
            return

        lead_data = session.get("lead_data", {})
        lead_id = lead_data.get("id", "unknown")

        response_text = await llm_client.call_llm(
            messages, 
            model=settings.GEMINI_MODEL,
            lead_id=lead_id,
            conversation_state=session["state"],
            phone=phone,
            company=lead_data.get("company", ""),
            response_format={"type": "json_object"}
        )
        
        bant_data = json.loads(response_text)
        
        # Update session
        session["bant_scores"] = bant_data
        
        # Additional flags
        if bant_data.get("overall_score", 0) >= 7:
            session["push_booking"] = True
        if bant_data.get("overall_score", 0) >= 9:
            session["escalate"] = True
            
        await redis_client.save_session(phone, session)
        
        # ── Update Tracker ─────────────────
        await tracker.update_state(
            lead_id=lead_id,
            current_state=session["state"],
            bant_budget=bant_data.get("budget", {}).get("evidence"),
            bant_authority=bant_data.get("authority", {}).get("evidence"),
            bant_need=bant_data.get("need", {}).get("evidence"),
            bant_timeline=bant_data.get("timeline", {}).get("evidence")
        )
        
        await tracker.update_signal_score(lead_id, bant_data.get("overall_score", 0))
        
        # Logic for temperature
        score = bant_data.get("overall_score", 0)
        temp = "Cold"
        if score >= 8: temp = "Hot"
        elif score >= 5: temp = "Warm"
        await tracker.update_temperature(lead_id, temp)
            
    except Exception as e:
        logger.error(f"Error extracting BANT for {phone}: {e}")

async def handle_bant_extraction(phone: str, message: str, history: List[Dict[str, str]], client_config: dict = None):
    """Wrapper to handle the extraction logic and message counting."""
    try:
        count_key = f"bant_count:{phone}"
        count = int(await redis_client.redis.get(count_key) or 0) + 1
        
        session = await redis_client.get_session(phone)
        current_state = session.get("state", "opening")
        
        if should_extract_bant(message, count, current_state):
            logger.info(f"[BANT] Triggering extraction for {phone} (Count: {count})")
            try:
                await extract_bant(phone, history, client_config)
            finally:
                # Always reset count — whether extraction succeeded or failed
                await redis_client.redis.set(count_key, "0")
        else:
            await redis_client.redis.set(count_key, str(count))
            logger.info(f"[BANT] Skipping extraction for {phone} (Count: {count})")
            
    except Exception as e:
        logger.error(f"Error in handle_bant_extraction: {e}")
