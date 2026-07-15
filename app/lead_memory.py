"""
app/lead_memory.py
==================
ADR 0003 Phase 3 — Structured Conversation Memory.

Replaces the lossy full-history "summary blob" with a compact, structured
`lead_memory` record that is updated each turn (inject -> distill -> trim ->
consolidate). The record persists the facts a sales rep must never forget:
name, company, industry, lead source, volume, pains (in the lead's own words),
objections already raised, attitude to AI, commitments made, and booking status.

Two pure, unit-testable helpers (no I/O):
  - `merge_memory(existing, new)` : deterministic merge of a distilled delta.
  - `format_memory_block(mem)`    : compact prompt block (returns "" if empty).

One I/O helper:
  - `distill_and_update(...)`     : cheap background LLM call that extracts a
                                    delta from the latest turn and merges it in.

Everything is gated by `settings.USE_STRUCTURED_MEMORY` at the call sites, and
is schema-additive: existing sessions with no `lead_memory` key default cleanly.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Scalar fields: overwritten only when the distilled value is non-empty.
_SCALAR_FIELDS = [
    "name", "company", "industry", "lead_source", "volume",
    "ai_attitude", "booking_status", "notes",
]
# List fields: unioned (deduped, order-preserving), never dropped.
_LIST_FIELDS = ["pains", "objections_raised", "commitments"]


def default_memory() -> Dict[str, Any]:
    """A fresh, empty lead_memory record."""
    mem: Dict[str, Any] = {f: "" for f in _SCALAR_FIELDS}
    for f in _LIST_FIELDS:
        mem[f] = []
    return mem


def _clean_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _clean_list(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = [v]
    out = []
    for item in v:
        s = _clean_str(item)
        if s:
            out.append(s)
    return out


def merge_memory(existing: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge a distilled delta into the existing record.

    Rules (deterministic, never destructive):
      - Scalars: keep existing unless `new` provides a non-empty value.
      - Lists:   union existing + new, dedup case-insensitively, preserve order.
    A wrong-but-empty delta can never wipe a known fact.
    """
    merged = default_memory()
    existing = existing or {}
    new = new or {}

    for f in _SCALAR_FIELDS:
        new_val = _clean_str(new.get(f))
        merged[f] = new_val or _clean_str(existing.get(f))

    for f in _LIST_FIELDS:
        combined = _clean_list(existing.get(f)) + _clean_list(new.get(f))
        seen = set()
        deduped = []
        for item in combined:
            key = item.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        merged[f] = deduped

    return merged


def sanitize_lead_name(name: Any) -> str:
    """Drop the agent's own name if a distill wrongly returns it as the lead's
    name. Returns '' for blocked names, else the cleaned name."""
    n = _clean_str(name)
    if n.lower() in _AGENT_NAME_BLOCKLIST:
        return ""
    return n


def is_empty(mem: Optional[Dict[str, Any]]) -> bool:
    if not mem:
        return True
    for f in _SCALAR_FIELDS:
        if _clean_str(mem.get(f)):
            return False
    for f in _LIST_FIELDS:
        if _clean_list(mem.get(f)):
            return False
    return True


_LABELS = {
    "name": "Name",
    "company": "Company",
    "industry": "Industry",
    "lead_source": "Lead source",
    "volume": "Lead volume",
    "ai_attitude": "Attitude to AI",
    "booking_status": "Booking status",
    "notes": "Notes",
    "pains": "Pains (their words)",
    "objections_raised": "Objections raised",
    "commitments": "Commitments made",
}


def format_memory_block(mem: Optional[Dict[str, Any]]) -> str:
    """Render a compact prompt block. Returns '' when there's nothing to say.
    Only non-empty fields are printed, so the block stays a few hundred tokens."""
    if is_empty(mem):
        return ""
    mem = mem or {}
    lines = ["\u2550\u2550\u2550 LEAD MEMORY (persistent, do not re-ask what's known) \u2550\u2550\u2550"]
    for f in _SCALAR_FIELDS:
        val = _clean_str(mem.get(f))
        if val:
            lines.append(f"{_LABELS[f]}: {val}")
    for f in _LIST_FIELDS:
        vals = _clean_list(mem.get(f))
        if vals:
            lines.append(f"{_LABELS[f]}: " + "; ".join(vals))
    return "\n".join(lines)


# The AI agent is named "Mark" / "Markeye". Its name must NEVER be recorded as
# the lead's name. Deterministic guard below backs up the prompt instruction.
_AGENT_NAME_BLOCKLIST = {"mark", "markeye", "mark ai", "markeye ai", "assistant", "ai", "bot"}

_DISTILL_PROMPT = (
    "You extract structured sales memory from ONE WhatsApp conversation turn.\n"
    "The turn has two speakers:\n"
    "  LEAD  = the prospect (the customer). Extract facts about THEM.\n"
    "  AGENT = the AI sales rep, named Mark, from Markeye. This is NOT the lead.\n"
    "Return ONLY a JSON object with these keys (omit a key or use \"\" / [] if unknown):\n"
    '{\n'
    '  "name": "", "company": "", "industry": "",\n'
    '  "lead_source": "", "volume": "",\n'
    '  "ai_attitude": "", "booking_status": "",\n'
    '  "pains": [], "objections_raised": [], "commitments": [], "notes": ""\n'
    '}\n'
    "Rules:\n"
    "- Record ONLY facts about the LEAD, actually stated in the turn. Never invent.\n"
    "- name: the LEAD's own name, only if THE LEAD states it about themselves. "
    "NEVER use 'Mark', 'Markeye', or the agent/assistant as the name. If unsure, leave \"\".\n"
    "- company / industry: the lead's business and sector.\n"
    "- volume: any lead/enquiry counts the lead mentions (e.g. '200 leads a month').\n"
    "- pains: the lead's problems in their own short words (e.g. 'leads go cold overnight'). "
    "ALWAYS put stated problems here, not only in notes.\n"
    "- objections_raised: lead concerns about price, timing, trust, AI, etc.\n"
    "- commitments: things the LEAD agreed to (e.g. 'agreed to a call', 'will send details').\n"
    "- booking_status: one of not_discussed | interested | agreed | booked | declined.\n"
    "- ai_attitude: short phrase e.g. 'skeptical', 'curious', 'enthusiastic'.\n"
    "- Extract ONLY new/updated facts from this turn; prior facts are already stored.\n"
)


async def distill_and_update(
    phone: str,
    message: str,
    response_text: str = "",
    client_config: Optional[dict] = None,
) -> None:
    """Background: distill the latest turn into a delta and merge into session
    `lead_memory`. Best-effort — any failure is logged and swallowed so it can
    never break a reply. Safe to run via asyncio.create_task."""
    # Local imports to avoid import cycles (llm -> redis -> ...).
    from app.redis_client import redis_client
    from app.llm import llm_client
    from app.config import settings

    if not settings.USE_STRUCTURED_MEMORY:
        return

    try:
        # lead_memory has its OWN Redis key — never stored inside the session
        # blob — so the concurrent BANT/persist session writers can't clobber it.
        existing = await redis_client.get_lead_memory(phone) or default_memory()

        # Seed known lead_data (name/company/industry) so memory is never blank
        # even before the first distill returns. Session is read-only here.
        session = await redis_client.get_session(phone)
        lead_data = (session or {}).get("lead_data", {}) or {}
        seed = {
            "name": lead_data.get("name") or lead_data.get("first_name") or "",
            "company": lead_data.get("company") or "",
            "industry": lead_data.get("industry") or "",
        }
        existing = merge_memory(existing, seed)

        turn_text = f"LEAD: {message}"
        if response_text:
            turn_text += f"\nAGENT (Mark, the AI rep, NOT the lead): {response_text}"

        messages = [
            {"role": "system", "content": _DISTILL_PROMPT},
            {"role": "user", "content": f"Latest turn:\n{turn_text}"},
        ]

        raw = await llm_client.call_llm(
            messages,
            lead_id=lead_data.get("id", "unknown"),
            conversation_state=(session or {}).get("state", "opening"),
            phone=phone,
            response_format={"type": "json_object"},
        )
        delta = json.loads(raw)

        # Deterministic guard: the agent's own name must never be stored as the
        # lead's name (backs up the prompt rule; distills sometimes ignore it).
        delta["name"] = sanitize_lead_name(delta.get("name"))

        merged = merge_memory(existing, delta)
        await redis_client.save_lead_memory(phone, merged)
        logger.info("[LeadMemory] Updated memory for %s", phone)

    except Exception as e:
        logger.error("[LeadMemory] distill_and_update failed for %s: %s", phone, e)
