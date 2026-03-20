import os
import time
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from app.config import settings
from app.tracker import AlbertTracker
from app.conversation_library import get_relevant_example
from app.signals import detect_personality_type, detect_objection_type
from app.redis_client import redis_client

tracker = AlbertTracker()

class LLMClient:
    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        self.helicone_key = settings.HELICONE_API_KEY

    def _get_client(self, lead_id: Optional[str], conversation_state: str, phone: str = "", company: str = "") -> AsyncOpenAI:
        """Helper to create a Helicone-instrumented OpenAI client."""
        # Coerce None values to empty strings — HTTP headers cannot be None
        safe_lead_id = lead_id or ""
        safe_phone = phone or ""
        safe_company = company or ""
        safe_state = conversation_state or "Opening"

        headers = {
            "HTTP-Referer": "https://after5.digital",
            "X-Title": "Albert by After5",
        }
        
        if self.helicone_key:
            headers.update({
                "Helicone-Auth": f"Bearer {self.helicone_key}",
                "Helicone-User-Id": safe_lead_id,
                "Helicone-Session-Id": f"conv_{safe_lead_id}",
                "Helicone-Property-Lead-Id": safe_lead_id,
                "Helicone-Property-Phone": safe_phone,
                "Helicone-Property-Company": safe_company,
                "Helicone-Property-State": safe_state,
                "Helicone-Property-Agent": "Albert",
                "Helicone-Property-Platform": "After5",
            })
            base_url = "https://openrouter.helicone.ai/api/v1"
        else:
            base_url = "https://openrouter.ai/api/v1"

        return AsyncOpenAI(
            base_url=base_url,
            api_key=self.api_key,
            default_headers=headers
        )

    def _estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimates USD cost based on token usage."""
        pricing = {
            "openai/gpt-4o":               {"prompt": 2.50,  "completion": 10.00},
            "openai/gpt-4o-mini":          {"prompt": 0.15,  "completion": 0.60},
            "anthropic/claude-3.5-sonnet": {"prompt": 3.00,  "completion": 15.00},
            "anthropic/claude-3-haiku":    {"prompt": 0.25,  "completion": 1.25},
        }
        # Default to gpt-4o rates if model not found
        rates = pricing.get(model, {"prompt": 2.50, "completion": 10.00})
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
        **kwargs
    ) -> str:
        """Calls OpenRouter via Helicone proxy and logs to Supabase."""
        model = model or settings.OPENROUTER_PRIMARY_MODEL
        client = self._get_client(lead_id, conversation_state, phone, company)
        
        start_time = time.time()
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                **kwargs
            )
            latency_ms = int((time.time() - start_time) * 1000)
            
            content = response.choices[0].message.content
            usage = response.usage
            cost = self._estimate_cost(model, usage.prompt_tokens, usage.completion_tokens)

            # Log to Supabase Tracker
            await tracker.log_llm_call(
                lead_id=lead_id,
                response_id=response.id,
                model=model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                conversation_state=conversation_state,
            )
            
            return content

        except Exception as e:
            print(f"[LLM Error] {model} call failed: {e}")
            # Simple fallback if primary fails
            if model != settings.OPENROUTER_FALLBACK_MODEL:
                print(f"[LLM] Falling back to {settings.OPENROUTER_FALLBACK_MODEL}")
                return await self.call_llm(
                    messages, 
                    model=settings.OPENROUTER_FALLBACK_MODEL,
                    lead_id=lead_id,
                    conversation_state=conversation_state,
                    phone=phone,
                    company=company,
                    **kwargs
                )
            raise e

    async def build_context(self, session: Dict[str, Any], lead_data: Dict[str, Any], message: str, knowledge_context: str = "") -> List[Dict[str, str]]:
        """Builds the full LLM context using static V3 system prompt."""
        # Always load static system prompt from file
        prompt_path = os.path.join(os.getcwd(), "prompts", "system_prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            core_prompt = f.read()

        # 1. Dynamic Industry Injection
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
            print(f"[LLM] ❌ Error getting relevant example: {e}", flush=True)

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
                role_label = "Lead" if msg["role"] == "user" else "Albert"
                history_lines.append(f"{role_label}: {msg['content']}")
            formatted_history = "\n".join(history_lines)
        else:
            formatted_history = "(no conversation yet)"

        replacements = {
            "{{lead_name}}": lead_data.get("name", lead_data.get("first_name", "there")),
            "{{lead_company}}": lead_data.get("company", "your company"),
            "{{current_state}}": session.get("state", "opening"),
            "{{calendly_link}}": settings.CALENDLY_LINK,
            "{{bant_scores}}": str(session.get("bant_scores", {})),
            "{{current_datetime}}": current_datetime,
            "{{conversation_history}}": formatted_history,
        }
        
        for key, value in replacements.items():
            system_prompt = system_prompt.replace(key, str(value))

        messages = [{"role": "system", "content": system_prompt}]
        for msg in session.get("history", []):
            messages.append(msg)
        messages.append({"role": "user", "content": message})
        
        return messages

llm_client = LLMClient()
