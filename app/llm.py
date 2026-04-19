import os
import time
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from app.config import settings
from app.tracker import MarkTracker
from app.conversation_library import get_relevant_example
from app.signals import detect_personality_type, detect_objection_type
from app.redis_client import redis_client

from app.llm_router import llm_router
from app.middleware import log_llm_call
import json

tracker = MarkTracker()

def _compute_scoring_status(session: dict, current_message: str) -> str:
    """
    Keyword-based qualification gate.
    All three signals must be present before Mark is allowed to push for booking.
    """
    current_state = session.get('state', 'opening')

    if current_state == 'escalation':
        return 'escalate_to_human'

    all_text = ' '.join(
        [m['content'] for m in session.get('history', [])] + [current_message]
    ).lower()

    lead_gen_keywords = [
        'leads', 'enquiries', 'submissions', 'forms', 'ads', 'google', 'meta',
        'facebook', 'instagram', 'referrals', 'organic', 'inbound',
        'calls', 'per month', 'a month', 'a week'
    ]
    has_lead_gen = any(kw in all_text for kw in lead_gen_keywords)

    pain_keywords = [
        'slow', 'missing', 'losing', 'after hours', 'evenings', 'weekends', 'overnight',
        'manual', 'inconsistent', 'going cold', 'cold', 'no one', 'nobody',
        'not following up', 'struggling', 'problem', 'issue', 'gap',
        'nightmare', 'frustrating', 'bottleneck', 'missed', 'taking too long'
    ]
    has_pain = any(kw in all_text for kw in pain_keywords)

    engagement_keywords = [
        'how much', 'cost', 'price', 'how long', 'how does', 'how would', 'what would',
        'integration', 'crm', 'interested', 'looks good', 'sounds good',
        'makes sense', 'want to', 'book', 'call', 'yes', 'yeah', 'exactly'
    ]
    has_engagement = any(kw in all_text for kw in engagement_keywords)

    if has_lead_gen and has_pain and has_engagement:
        return 'push_for_booking'

    return 'continue_discovery'



class LLMClient:
    """
    High-level LLM wrapper managing Markeye business logic, RAG context injection, 
    and prompt building.
    
    Delegates the actual API generation to the `llm_router` module (SmartLLMRouter) 
    which handles multi-provider fallbacks (Groq -> Gemini -> Cerebras).
    """
    def __init__(self):
        # We now use the router instead of a direct client
        self.router = llm_router

    def _estimate_cost(self, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimates USD cost based on token usage. Free tiers return 0.0."""
        if provider in ["Groq", "Gemini", "Cerebras"]:
            return 0.0  # Currently using free/fair-use tiers
            
        pricing = {
            "gpt-4o":               {"prompt": 2.50,  "completion": 10.00},
            "gpt-4o-mini":          {"prompt": 0.15,  "completion": 0.60},
        }
        # Default to gpt-4o-mini rates if model not found
        rates = pricing.get(model, {"prompt": 0.15, "completion": 0.60})
        total = (prompt_tokens * rates["prompt"] + completion_tokens * rates["completion"]) / 1_000_000
        return round(total, 6)

    async def call_llm(
        self, 
        messages: List[Dict[str, str]], 
        model: Optional[str] = None,
        lead_id: Optional[str] = None,
        conversation_state: str = "Opening",
        phone: str = "",
        company: str = "",
        client_config: Optional[dict] = None,
        **kwargs
    ) -> str:
        """Calls the SmartLLMRouter and logs to Supabase via the Tracker."""
        try:
            # The router handles the fallback logic (Groq -> Gemini -> Cerebras)
            result = await self.router.generate_completion(
                messages=messages,
                model_override=model,
                **kwargs
            )
            
            content = result["content"]
            model_used = result["model"]
            provider = result["provider"]
            usage = result["usage"]
            latency_ms = result["latency_ms"]
            
            # Prepare metadata for training tracking
            metadata = {
                "provider": provider,
                "model": model_used,
                "latency_ms": latency_ms,
                "tokens_in": usage.prompt_tokens if usage else 0,
                "tokens_out": usage.completion_tokens if usage else 0,
                "tokens_total": usage.total_tokens if usage else 0,
                "conversation_state": conversation_state
            }

            cost = self._estimate_cost(provider, model_used, usage.prompt_tokens, usage.completion_tokens)

            # Log to Supabase Tracker
            # We repurpose helicone_id field to store the provider name for now
            await tracker.log_llm_call(
                lead_id=lead_id,
                response_id=f"{provider}:{result['id']}",
                model=model_used,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                conversation_state=conversation_state,
            )
            
            # Structured Logging & Metrics
            await redis_client.log_llm_metric(provider, usage.total_tokens if usage else 0)
            log_llm_call(
                provider=provider,
                model=model_used,
                latency_ms=latency_ms,
                tokens_in=usage.prompt_tokens if usage else 0,
                tokens_out=usage.completion_tokens if usage else 0,
                success=True,
                client_id=lead_id or "unknown"
            )

            # Store metadata in Redis briefly so the caller (conversation.py) can pick it up for log_outbound
            if phone:
                try:
                    await redis_client.redis.setex(f"last_llm_usage:{phone}", 30, json.dumps(metadata))
                except Exception as e:
                    logger.warning(f"[LLM] Error saving usage to Redis: {e}")

            return content

        except Exception as e:
            logger.error(f"[LLM Client Error] Router failed all providers: {e}")
            raise e

    async def build_context(
        self, 
        session: Dict[str, Any], 
        lead_data: Dict[str, Any], 
        message: str, 
        knowledge_context: str = "",
        client_config: Optional[dict] = None
    ) -> List[Dict[str, str]]:
        """Builds the full LLM context using client_prompt or V4 system prompt."""
        # Multi-tenant: load client specific prompt or fallback to file
        core_prompt = ""
        if client_config and client_config.get("system_prompt"):
            core_prompt = client_config.get("system_prompt")
        else:
            prompt_path = os.path.join(os.getcwd(), "prompts", "system_prompt.txt")
            with open(prompt_path, "r", encoding="utf-8") as f:
                core_prompt = f.read()
            
        industry_context = ""
        industry = (lead_data.get("industry") or "").lower()
        industry_map = {
            "real_estate": "real_estate.txt",
            "property": "real_estate.txt",
            "ecommerce": "ecommerce.txt",
            "store": "ecommerce.txt",
            "legal": "legal.txt",
            "law": "legal.txt",
            "clinic": "clinics.txt",
            "dental": "clinics.txt"
        }

        # Check message for industry keywords too
        msg_lower = message.lower()
        for key, filename in industry_map.items():
            if key in industry or key in msg_lower:
                path = os.path.join(os.getcwd(), "prompts", "knowledge", filename)
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        industry_context = f"\n═══ INDUSTRY VERTICAL KNOWLEDGE ═══\n" + f.read()
                break

        # 2. Dynamic Objection Injection
        objection_context = ""
        objection_map = {
            "price": "price.txt",
            "cost": "price.txt",
            "avoiding": "price_pressure.txt",
            "bad": "bad_experience.txt",
            "awful": "bad_experience.txt",
            "chatgpt": "tools_comparison.txt",
            "zapier": "tools_comparison.txt",
            "manychat": "tools_comparison.txt",
            "budget": "no_budget.txt",
            "work": "ai_failure_fear.txt",
            "sales team": "sales_team.txt",
            "business hours": "business_hours.txt",
            "weekends": "business_hours.txt",
            "case study": "proof.txt",
            "proof": "proof.txt",
            "setup": "setup_time.txt",
            "how long": "setup_time.txt",
            "crm": "crm.txt",
            "hubspot": "crm.txt",
            "ready": "ai_readiness.txt",
            "ai isn't": "ai_readiness.txt",
            "few months": "delayed_action.txt",
            "small business": "small_business.txt",
            "too good": "skepticism.txt",
            "wrong": "ai_errors.txt",
            "error": "ai_errors.txt",
            "not interested": "not_interested.txt",
            "manually": "manual_process.txt",
            "more information": "send_info.txt"
        }

        found_objections = []
        for key, filename in objection_map.items():
            if key in msg_lower:
                path = os.path.join(os.getcwd(), "prompts", "objections", filename)
                if os.path.exists(path) and filename not in found_objections:
                    with open(path, "r", encoding="utf-8") as f:
                        objection_context += f.read() + "\n"
                    found_objections.append(filename)

        if objection_context:
            objection_context = "\n═══ OBJECTION HANDLING (DIAGNOSTIC) ═══\n" + objection_context

        # 3. Dynamic Example Injection (Phase 2)
        example_context = ""
        try:
            # Detect signals for example matching
            history_contents = [m["content"] for m in session.get("history", []) if m["role"] == "user"]
            personality = detect_personality_type(history_contents)
            objection = detect_objection_type(message)
            
            example = await get_relevant_example(
                redis_client.redis,
                industry=industry,
                stage=session.get("state", "opening"),
                objection=objection,
                personality=personality
            )
            
            if example:
                example_context = f"\n═══ REFERENCE CONVERSATION (LEARN FROM THIS TONE) ═══\n{example}\n"
        except Exception as e:
            logger.error(f"[LLM] Error getting relevant example: {e}")

        # Combine Core + Addons (Industry, Objections, Examples)
        system_prompt = core_prompt
        
        # Industry Injection
        if "{{DYNAMIC_KNOWLEDGE}}" in system_prompt:
            system_prompt = system_prompt.replace("{{DYNAMIC_KNOWLEDGE}}", industry_context)
        elif industry_context:
            system_prompt += f"\n\n{industry_context}"
            
        # Objection Injection
        if "{{DYNAMIC_OBJECTIONS}}" in system_prompt:
            system_prompt = system_prompt.replace("{{DYNAMIC_OBJECTIONS}}", objection_context)
        elif objection_context:
            system_prompt += f"\n\n{objection_context}"
            
        # Example Injection
        if "{{DYNAMIC_EXAMPLES}}" in system_prompt:
            system_prompt = system_prompt.replace("{{DYNAMIC_EXAMPLES}}", example_context)
        elif example_context:
            system_prompt += f"\n\n{example_context}"

        # Inject RAG context if available (Standard RAG)
        if knowledge_context:
            rag_block = f"\n\n### KNOWLEDGE BASE CONTEXT (USE THIS TO ANSWER DISCOVERY QUESTIONS):\n{knowledge_context}\n\n"
            system_prompt = rag_block + system_prompt

        # Replace placeholders
        current_datetime = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Format conversation history
        history = session.get("history", [])
        if history:
            history_lines = []
            for msg in history:
                role_label = "Lead" if msg["role"] == "user" else "Mark"
                history_lines.append(f"{role_label}: {msg['content']}")
            formatted_history = "\n".join(history_lines)
        else:
            formatted_history = "(no conversation yet)"

        # V4: keyword-based scoring_status — inspects conversation history for qualification signals
        current_state_val = session.get("state", "opening")
        scoring_status = _compute_scoring_status(session, message)

        replacements = {
            "{{lead_name}}": lead_data.get("name", lead_data.get("first_name", "there")),
            "{{lead_company}}": lead_data.get("company", "your company"),
            "{{lead_industry}}": lead_data.get("industry", "their industry"),
            "{{lead_company_summary}}": lead_data.get("form_message", ""),
            "{{current_state}}": current_state_val,
            "{{scoring_status}}": scoring_status,
            "{{calendly_link}}": (client_config.get("calcom_link") or client_config.get("calendly_link")) if client_config else settings.booking_link,
            "{{booking_link}}": (client_config.get("calcom_link") or client_config.get("calendly_link")) if client_config else settings.booking_link,
            "{{business_name}}": client_config.get("business_name") if client_config else "Markeye",
            "{{bant_scores}}": str(session.get("bant_scores", {})),
            "{{current_datetime}}": current_datetime,
            "{{conversation_history}}": formatted_history,
        }
        
        for key, value in replacements.items():
            system_prompt = system_prompt.replace(key, str(value))

        messages = [{"role": "system", "content": system_prompt}]
        
        # 4. Sliding Window & Summarization (V8)
        full_history = session.get("history", [])
        MAX_CONTEXT = 10
        
        if len(full_history) > MAX_CONTEXT:
            logger.info(f"[LLM] 💨 History > {MAX_CONTEXT}. Summarizing older turns for {lead_id}...")
            
            # Check for cached summary
            summary_key = f"summary:{lead_id}"
            summary = await redis_client.redis.get(summary_key)
            
            if not summary:
                # Summarize turns before the last 10
                to_summarize = full_history[:-MAX_CONTEXT]
                summary_prompt = (
                    "Summarize this sales conversation in 2-3 sentences. "
                    "Include: lead name, business type, pain points, and any BANT signals detected."
                )
                history_text = "\n".join([f"{m['role']}: {m['content']}" for m in to_summarize])
                
                # Use Gemini-Flash for cheap summarization
                summary_res = await self.router.generate_completion(
                    messages=[
                        {"role": "system", "content": summary_prompt},
                        {"role": "user", "content": f"History to summarize:\n{history_text}"}
                    ],
                    model_override=settings.GEMINI_MODEL
                )
                summary = summary_res["content"]
                await redis_client.redis.setex(summary_key, 3600, summary) # Cache for 1h
                
            messages.append({"role": "system", "content": f"═══ EARLIER CONVERSATION SUMMARY ═══\n{summary}"})
            recent_context = full_history[-MAX_CONTEXT:]
        else:
            recent_context = full_history

        for msg in recent_context:
            messages.append(msg)
            
        messages.append({"role": "user", "content": message})
        
        return messages

llm_client = LLMClient()
