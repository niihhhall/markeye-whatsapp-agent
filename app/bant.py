import json
import os
from typing import List, Dict, Any
from app.config import settings
from app.llm import llm_client
from app.redis_client import redis_client

async def extract_bant(phone: str, history: List[Dict[str, str]]):
    """Background BANT extraction using a cheap LLM model."""
    prompt_path = os.path.join(os.getcwd(), "prompts", "bant_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        bant_prompt = f.read()

    # Format history for prompt
    history_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history])
    system_prompt = bant_prompt.replace("{{conversation_history}}", history_text)

    messages = [{"role": "system", "content": system_prompt}]

    try:
        response_text = await llm_client.call_llm(messages, model=settings.OPENROUTER_BANT_MODEL)
        # Clean response text in case LLM added markdown backticks
        response_text = response_text.replace("```json", "").replace("```", "").strip()
        bant_data = json.loads(response_text)
        
        # Load current session
        session = await redis_client.get_session(phone)
        if session:
            session["bant_scores"] = bant_data
            
            # Additional flags
            if bant_data.get("overall_score", 0) >= 7:
                session["push_booking"] = True
            if bant_data.get("overall_score", 0) >= 9:
                session["escalate"] = True
                
            await redis_client.save_session(phone, session)
            
    except Exception as e:
        print(f"Error extracting BANT for {phone}: {e}")
