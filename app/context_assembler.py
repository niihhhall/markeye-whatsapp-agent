"""
app/context_assembler.py
========================
ADR 0001 Phase 2 — layered, selectively-loaded context assembly.

Composes Mark's base system prompt from modular layer files instead of one
41k-char monolith. The always-on layers (soul, facts, playbook, style) are
small; the large KNOWLEDGE layer is injected ONLY when the lead's message is
actually about a knowledge-base topic. This cuts per-reply input tokens
without losing grounding (the Truth Boundary in facts.md still forces "push to
the call" when a fact isn't present).

Pure module: only reads files, no Redis/Supabase/LLM. Fully unit-testable
(see tests/test_context_assembly.py). Wiring into build_context happens in a
separate, fallback-guarded step (Phase 2 Step 2).
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

LAYERS_DIR = os.path.join(os.getcwd(), "prompts", "layers")

# Always-on layers, in prompt order (identity first — models weight the top).
_ALWAYS_ON = ["soul", "facts", "playbook", "style"]
# Large, selectively-loaded layer.
_KNOWLEDGE = "knowledge"

# If any of these appear in the lead's message (or recent text), inject the
# knowledge layer. Deliberately broad — when unsure we INCLUDE (safe: more
# tokens, never less grounding).
KB_KEYWORDS = [
    "website", "markeye.io", "demo", "call", "book", "dashboard", "analytics",
    "price", "pricing", "cost", "how much", "integration", "integrate", "crm",
    "hubspot", "salesforce", "channel", "sms", "email", "whatsapp",
    "onboard", "contract", "terms", "language", "security", "data", "gdpr",
    "industry", "tech", "model", "built", "how does it work", "under the hood",
    "case study", "case studies", "fallback", "handoff", "human", "apexai",
    "build time", "how long", "running cost", "refund", "guarantee",
]

# Runtime context block — holds the per-conversation placeholders that
# build_context() substitutes at call time. The legacy monolith carried this
# inline; the layer files don't, so the assembler appends it. Conversation
# history is NOT here — it's appended as the message list by build_context.
RUNTIME_CONTEXT_TEMPLATE = (
    "═══ CURRENT CONVERSATION CONTEXT ═══\n"
    "Lead Name: {{lead_name}}\n"
    "Lead Company: {{lead_company}}\n"
    "Lead Industry: {{lead_industry}}\n"
    "Company Summary: {{lead_company_summary}}\n"
    "Business: {{business_name}}\n"
    "Current State: {{current_state}}\n"
    "Scoring Status: {{scoring_status}}\n"
    "Current Date/Time: {{current_datetime}}\n"
    "Booking Link: {{booking_link}}\n\n"
    "Scoring status values: continue_discovery, push_for_booking, escalate_to_human. Follow them."
)

_cache: dict[str, str] = {}


def _load(name: str) -> str:
    """Read a layer file (cached). Returns '' if missing."""
    if name in _cache:
        return _cache[name]
    path = os.path.join(LAYERS_DIR, f"{name}.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except FileNotFoundError:
        logger.warning("[ContextAssembler] Layer file missing: %s", path)
        text = ""
    _cache[name] = text
    return text


def clear_cache() -> None:
    _cache.clear()


def layers_available() -> bool:
    """True only if all always-on layers exist and are non-empty.
    build_context uses this to decide whether to use the assembler or fall
    back to the legacy monolith."""
    return all(_load(n) for n in _ALWAYS_ON)


def knowledge_relevant(text: str) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in KB_KEYWORDS)


def assemble_base_prompt(message: str, phase: str = "", include_knowledge: Optional[bool] = None) -> str:
    """Assemble the base system prompt from layers.

    include_knowledge: force include/exclude the knowledge layer; when None
    (default) it's decided by keyword relevance on `message`.
    Returns the composed prompt (placeholders like {{lead_name}} intact — the
    caller does the substitution).
    """
    parts = [_load(n) for n in _ALWAYS_ON if _load(n)]

    want_kb = knowledge_relevant(message) if include_knowledge is None else include_knowledge
    if want_kb:
        kb = _load(_KNOWLEDGE)
        if kb:
            # Insert knowledge before the final STYLE layer so response rules
            # stay at the end (recency bias keeps them salient).
            parts.insert(len(parts) - 1, kb)

    return "\n\n".join(parts)


def assemble_full_prompt(message: str, phase: str = "", include_knowledge: Optional[bool] = None) -> str:
    """Base layers + the runtime context block (with placeholders intact).
    This is what build_context() uses as the core prompt before substitution."""
    return assemble_base_prompt(message, phase, include_knowledge) + "\n\n" + RUNTIME_CONTEXT_TEMPLATE
