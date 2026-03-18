"""
Dynamic Prompt Assembler for Albert.
Merges static base modules with live training overrides.
"""

import json
import logging
import time
from typing import Dict, Optional
from app.supabase_client import supabase_client
from app.redis_client import redis_client
from app.config import settings

logger = logging.getLogger(__name__)

# ============================================================
# BASE PROMPT MODULES (Static Defaults from V3)
# ============================================================
# These are the FALLBACK values. Training data overrides these.

BASE_MODULES: Dict[str, str] = {

    "identity": """
## IDENTITY
You are Albert, the Senior AI Business Development Consultant for After5 Digital.
After5 Digital is a premier digital marketing agency based in London, UK.
Your objective: engage high-value leads, qualify them via the BANT framework, showcase our technical expertise, handle high-level objections, and secure discovery calls for our founder, Louis.
You report directly to Louis.
""",

    "tone_and_voice": """
## TONE & VOICE
- Speak in **Sophisticated British English**.
- Be impeccable, polite, and authoritative—like a senior partner at a top-tier consultancy.
- Use British professional phrasing naturally (e.g., "Cheers," "Brilliant," "Spot on," "Shall we," "I suspect").
- Maintain a calm, high-status, and confident demeanor.
- **NEVER** use emojis in professional correspondence.
- Keep messages punchy and concise—respect the client's time.
- Match the client's energy, but never lose your professional composure.
""",

    "greeting_rules": """
## GREETING RULES
- First-time contact: Formal but warm introduction. "Good morning/afternoon, Albert here from After5."
- Returning contact: Acknowledge previous discussions. "Good to see you again. I trust things are progressing well."
- Referral: "Thank you for reaching out. [Name] mentioned we should connect."
""",

    "discovery_rules": """
## DISCOVERY (SPIN Framework)
- Situation: Understand their current market position in the UK/EU.
- Problem: Identify core pain points in their digital infrastructure.
- Implication: Briefly highlight the cost of inaction.
- Need-payoff: Guide them toward seeing the ROI of our solutions.
- Max 2-3 precise questions per message.
""",

    "sales_approach": """
## SALES APPROACH
- Value-First: Focus on competitive advantage and scalability.
- Social Proof: Reference UK/Global case studies and high-performing digital assets.
- Methodology: Challenger Sale (teach, tailor, take control).
- Strategy: Position After5 as a strategic partner, not just a service provider.
""",

    "objection_handling": """
## OBJECTION HANDLING
- Strategy: Empathize → Reframe → Evidence.
- "I understand your concern about [X]; however, we've found that [Evidence-based insight]."
- Never sound defensive. Maintain an "educational" tone.
- Handle: Price pressure, Long-term commitment, Technical complexity, "Need to talk to the Board."
""",

    "closing_techniques": """
## CLOSING & NEXT STEPS
- Logical Sequence: Recapitulate value → Suggest a brief Discovery Call.
- "Does it make sense to have a quick 15-minute chat with Louis to explore this in detail?"
- Assumptive Close: "I'll send across the initial brief, and we can discuss the rollout next Tuesday?"
- Always end with a clear, single path forward.
""",

    "followup_rules": """
## FOLLOW-UP PROTOCOL
- 24h: Professional nudge. "I wanted to ensure you received my previous note regarding [X]."
- 3-5 Days: Insight-led follow-up. Share a relevant industry update or competitor analysis.
- 7 Days: polite "Low priority" check. "I'll assume this isn't a priority at the moment; feel free to reach out when ready."
""",

    "pricing_info": """
## PRICING
- Discuss pricing only once the value and scope are clearly defined.
- Present custom solutions for enterprise-level needs.
- Reference "Investment" rather than "Price" or "Cost".
- Focus on Lifetime Value (LTV) and ROI.
""",

    "services_info": """
## SERVICES (UK Focused)
- Bespoke Web Development (Custom Next.js, headless CMS, High-performance E-comm).
- Performance Marketing (SEO, Google Ads, Meta Ads).
- AI & Automation Strategy (Internal workflows, customer-facing AI).
- Brand Architecture & Visual Identity.
""",

    "escalation_rules": """
## ESCALATION
Transfer to Louis when:
- High-level contract negotiations are required.
- Specific technical queries beyond Albert's current knowledge base.
- Lead signals High Intent for immediate sign-off.
- Any legal or GDPR-related inquiries.
""",

    "personality_handling": """
## CLIENT SEGMENTATION
- Analytical: Detailed specs, clear KPIs, no fluff.
- Driver: Results, timelines, directness. Skip the pleasantries.
- Expressive: Vision, innovation, "the big picture".
- Amiable: Relationship-led, trust-building, steady pace.
""",

    "recovery_patterns": """
## LEAD RECOVERY
- Dormant leads: "I was reviewing your case and noticed [Market Shift]. Thought this might be of interest."
- Re-opening: "I suspected the timing might have been off previously. Is digital growth back on your radar?"
""",

    "state_management": """
## CONVERSATION FLOW
- NEW: Professional greeting & initial hook.
- DISCOVERY: Information gathering.
- QUALIFIED: Confirming BANT alignment.
- BOOKING: Securing the call with Louis.
- CLOSED: Onboarding or nurtured.
""",

    "safety_rules": """
## ⛔ SAFETY & TRUTH BOUNDARY
- Strict GDPR compliance.
- Never guarantee #1 Google ranking (unethical/dishonest).
- Never share other clients' private analytics or growth data.
- Never pretend to be a "human" if asked directly.
- Protect the After5 brand reputation at all costs.
""",

    "response_format": """
## RESPONSE FORMAT
- Impeccable grammar and British spelling (e.g., 'optimisation').
- Structured paragraphs (max 3 per message).
- **Keep greetings (e.g., "Hello") in the same block as the opening sentence.**
- Avoid unnecessary newlines that cause frequent message bubbles.
- Bullet points for lists.
- ONE clear, professional question or call-to-action per response.
""",

    "business_context": """
## CURRENT BUSINESS CONTEXT
- Focused on high-growth SMEs and Enterprise clients in London and the UK.
- Current priority: SEO & Custom Development projects.
- Use 'Louis' as the final expert authority.
""",
}

# Modules that training data can NEVER override
LOCKED_MODULES = {"safety_rules", "identity"}


# ============================================================
# TRAINING → MODULE MAPPING
# ============================================================
# Maps training categories to which prompt module they override

TRAINING_TO_MODULE_MAP = {
    "tone":         "tone_and_voice",
    "voice":        "tone_and_voice",
    "greeting":     "greeting_rules",
    "sales":        "sales_approach",
    "objection":    "objection_handling",
    "closing":      "closing_techniques",
    "followup":     "followup_rules",
    "qna":          None,  # QnA doesn't override a module — it's injected separately
    "personality":  "personality_handling",
    "recovery":     "recovery_patterns",
    "escalation":   "escalation_rules",
    "context":      "business_context",
}


# ============================================================
# PROMPT ASSEMBLER
# ============================================================

class PromptAssembler:
    """Builds Albert's final system prompt by merging base + training."""

    CACHE_KEY = "albert:training_overrides"
    CACHE_TTL = 300  # 5 minutes

    def __init__(self):
        # We use redis_client directly for caching as requested
        pass

    async def build_prompt(self, customer_message: str, conversation_state: str = "NEW") -> str:
        """
        Build the complete system prompt for Albert.

        Steps:
        1. Start with base modules
        2. Fetch ALL active training data (cached)
        3. For each training category, build override content
        4. Replace base module with training override (if exists)
        5. Add message-specific QnA matches
        6. Assemble final prompt
        """

        # Step 1: Copy base modules
        final_modules = dict(BASE_MODULES)

        # Step 2: Fetch active training overrides (grouped by category)
        training_overrides = await self._get_training_overrides()

        # Step 3: Apply overrides (skip locked modules)
        for train_cat, module_key in TRAINING_TO_MODULE_MAP.items():
            if module_key is None:
                continue  # QnA handled separately
            if module_key in LOCKED_MODULES:
                continue  # Safety rules never overridden

            entries = training_overrides.get(train_cat, [])
            if entries:
                override_content = self._build_module_override(
                    module_key, train_cat, entries
                )
                final_modules[module_key] = override_content

        # Step 4: Add message-specific QnA matches
        qna_block = await self._get_relevant_qna(customer_message)

        # Step 5: Add state-specific guidance
        state_block = await self._get_state_guidance(conversation_state, training_overrides)

        # Step 6: Assemble final prompt
        prompt_parts = []

        # Fixed order for consistency
        module_order = [
            "identity",
            "safety_rules",
            "tone_and_voice",
            "response_format",
            "business_context",
            "services_info",
            "pricing_info",
            "greeting_rules",
            "discovery_rules",
            "sales_approach",
            "objection_handling",
            "closing_techniques",
            "followup_rules",
            "personality_handling",
            "recovery_patterns",
            "escalation_rules",
            "state_management",
        ]

        for key in module_order:
            if key in final_modules:
                prompt_parts.append(final_modules[key])

        # Add dynamic sections at the end
        if state_block:
            prompt_parts.append(state_block)
        if qna_block:
            prompt_parts.append(qna_block)

        final_prompt = "\n".join(prompt_parts)

        # Step 7: Token safety — truncate if too long
        final_prompt = self._enforce_token_limit(final_prompt, max_tokens=3500)

        return final_prompt

    async def _get_training_overrides(self) -> Dict[str, list]:
        """Fetch all active training entries grouped by category with Redis cache."""
        try:
            # Check cache first
            cached = await redis_client.get(self.CACHE_KEY)
            if cached:
                return json.loads(cached)

            # Fetch from Supabase
            client = await supabase_client.get_client()
            result = await client.table("dynamic_training") \
                .select("category,scenario,ideal_response,priority,metadata") \
                .eq("is_active", True) \
                .order("priority", desc=True) \
                .execute()

            grouped = {}
            for entry in (result.data or []):
                cat = entry["category"]
                if cat not in grouped:
                    grouped[cat] = []
                grouped[cat].append(entry)

            # Store in cache
            await redis_client.set(self.CACHE_KEY, json.dumps(grouped), ex=self.CACHE_TTL)
            return grouped
        except Exception as e:
            logger.error(f"[PromptAssembler] Error fetching training overrides: {e}")
            return {}

    async def invalidate_cache(self):
        """Call this after any /train command to refresh cache."""
        try:
            await redis_client.redis.delete(self.CACHE_KEY)
            logger.info("[PromptAssembler] Cache invalidated")
        except Exception as e:
            logger.error(f"[PromptAssembler] Cache invalidation failed: {e}")

    def _build_module_override(self, module_key: str, train_cat: str, entries: list) -> str:
        """
        Build a module section from training entries.
        This REPLACES the base module content.
        """
        header_map = {
            "tone_and_voice":      "## TONE & VOICE (Live Trained)",
            "greeting_rules":      "## GREETING RULES (Live Trained)",
            "sales_approach":      "## SALES APPROACH (Live Trained)",
            "objection_handling":  "## OBJECTION HANDLING (Live Trained)",
            "closing_techniques":  "## CLOSING (Live Trained)",
            "followup_rules":      "## FOLLOW-UP RULES (Live Trained)",
            "personality_handling": "## CUSTOMER PERSONALITIES (Live Trained)",
            "recovery_patterns":   "## LEAD RECOVERY (Live Trained)",
            "escalation_rules":    "## ESCALATION (Live Trained)",
            "business_context":    "## CURRENT BUSINESS CONTEXT (Live Updated)",
        }

        header = header_map.get(module_key, f"## {module_key.upper()} (Live Trained)")
        lines = [header, ""]

        # For tone/voice — combine rules into guidelines
        if train_cat in ("tone", "voice"):
            lines.append("Guidelines from training:")
            for entry in entries[:5]:  # Max 5 entries per module
                if entry.get("notes"):
                    lines.append(f"- {entry.get('scenario', 'General')}: {entry.get('ideal_response', '')}")
                    lines.append(f"  Note: {entry['notes']}")
                else:
                    lines.append(f"- {entry.get('scenario', 'General')}: {entry.get('ideal_response', '')}")

        # For technique-based modules — show patterns
        elif train_cat in ("objection", "closing", "sales", "recovery"):
            lines.append("Learned patterns:")
            for entry in entries[:6]:
                lines.append(f"\nScenario: \"{entry.get('scenario', '')}\"")
                lines.append(f"Response approach: \"{entry.get('ideal_response', '')}\"")
                if entry.get("notes"):
                    lines.append(f"Technique: {entry['notes']}")

        # For rule-based modules — show as rules
        elif train_cat in ("greeting", "followup", "escalation"):
            lines.append("Active rules:")
            for entry in entries[:5]:
                sub = f" ({entry.get('subcategory', '')})" if entry.get("subcategory") else ""
                lines.append(f"- {entry.get('scenario', '')}{sub}: {entry.get('ideal_response', '')}")

        # For context — show latest updates
        elif train_cat == "context":
            lines.append("Latest updates:")
            for entry in entries[:5]:
                lines.append(f"- {entry.get('scenario', '')}")
                lines.append(f"  Impact: {entry.get('ideal_response', '')}")
                if entry.get("notes"):
                    lines.append(f"  {entry['notes']}")

        # For personality — show per-type guidance
        elif train_cat == "personality":
            for entry in entries[:4]:
                ptype = entry.get("subcategory", "general")
                lines.append(f"\n{ptype.upper()} customer:")
                lines.append(f"- Approach: {entry.get('ideal_response', '')}")

        # Generic fallback
        else:
            for entry in entries[:5]:
                lines.append(f"- {entry.get('scenario', 'General')}: {entry.get('ideal_response', '')}")

        # Add base module content as fallback guidelines
        base_content = BASE_MODULES.get(module_key, "")
        if base_content:
            lines.append("")
            lines.append("Fallback guidelines (use if training doesn't cover a specific case):")
            # Add only first 3 lines of base as fallback
            base_lines = [l for l in base_content.strip().split("\n") if l.strip() and not l.startswith("##")]
            for bl in base_lines[:3]:
                lines.append(f"  {bl.strip()}")

        return "\n".join(lines)

    async def _get_relevant_qna(self, customer_message: str) -> str:
        """Fetch QnA entries matching customer message keywords."""
        import re
        if not customer_message:
            return ""
            
        stop_words = {"hai", "ka", "ki", "ke", "ko", "se", "mein", "kya",
                      "aur", "ya", "toh", "bhi", "nahi", "ho", "hain",
                      "the", "is", "a", "an", "in", "for", "and", "or"}
        words = re.findall(r'\w+', customer_message.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]

        if not keywords:
            return ""

        try:
            client = await supabase_client.get_client()
            results = await client.table("dynamic_training") \
                .select("scenario,ideal_response,metadata") \
                .eq("category", "qna") \
                .eq("is_active", True) \
                .ilike("scenario", f"%{keywords[0]}%") \
                .order("priority", desc=True) \
                .limit(3) \
                .execute()
        except Exception as e:
            logger.error(f"[PromptAssembler] QnA lookup failed: {e}")
            return ""

        if not results.data:
            return ""

        lines = ["\n## RELEVANT Q&A (from training)"]
        for entry in results.data:
            lines.append(f"\nQ: {entry.get('scenario', '')}")
            lines.append(f"A: {entry.get('ideal_response', '')}")

        lines.append("\nNote: Adapt these answers naturally. Don't copy word-for-word.")
        return "\n".join(lines)

    async def _get_state_guidance(self, state: str, training_overrides: dict) -> str:
        """Get state-specific guidance combining base + training."""
        state_hints = {
            "NEW":         "Customer just arrived. Focus on greeting and opening discovery.",
            "DISCOVERY":   "Currently understanding needs. Ask smart questions, don't pitch yet.",
            "PITCHED":     "Proposal shared. Be ready for objections. Follow up if no response.",
            "NEGOTIATION": "Customer is comparing/deciding. Handle objections, show value.",
            "BOOKING":     "Lead is booking. Provide the Calendly link if not sent. IMPORTANT: If lead says 'I booked it', do NOT confirm. Say: 'Great! I'll check my system and confirm once the notification hits my desk.'",
            "CLOSED_WON":  "Deal done! Confirm onboarding steps. Be excited but professional.",
            "CLOSED_LOST": "Didn't convert. Be gracious. Leave door open for future.",
            "FOLLOWUP":    "Follow-up mode. Add value with each touch. Don't be annoying.",
            "WAITING":     "Customer needs to respond. Don't double-message too soon.",
            "ESCALATED":   "Handed to human. Support the handoff, don't re-engage as Albert.",
        }

        hint = state_hints.get(state, "")
        if not hint:
            return ""

        return f"\n## CURRENT STATE: {state}\n{hint}"

    def _enforce_token_limit(self, prompt: str, max_tokens: int = 3500) -> str:
        """
        Rough token limit enforcement.
        ~4 chars per token approximation.
        If prompt is too long, trim lower-priority sections.
        """
        estimated_tokens = len(prompt) // 4

        if estimated_tokens <= max_tokens:
            return prompt

        # Trim strategy: remove fallback guidelines first
        lines = prompt.split("\n")
        trimmed = []
        skip_fallback = False

        for line in lines:
            if "Fallback guidelines" in line:
                skip_fallback = True
                continue
            if skip_fallback and (line.strip().startswith("##") or line.strip().startswith("\n##")):
                skip_fallback = False
            if not skip_fallback:
                trimmed.append(line)

        final_prompt = "\n".join(trimmed)
        if len(final_prompt) // 4 > max_tokens:
            # Hard truncate if still too long
            return final_prompt[:max_tokens * 4]
            
        return final_prompt


# ============================================================
# SINGLETON INSTANCE
# ============================================================
prompt_assembler = PromptAssembler()
