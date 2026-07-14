# ADR 0003 — Structured Conversation Memory

- **Status:** Accepted (Option B shipped, gated behind `USE_STRUCTURED_MEMORY`, default off)
- **Date:** 2026-07-14
- **Owner:** Engineering (CTO advisory)
- **Deciders:** Founder, Engineering
- **Related:** ADR 0001 (Layered Context Architecture — this is its "Memory" layer), ADR 0002 (Eval Harness)

---

## Context

Mark's per-conversation memory today lives in `session:{phone}` in Redis:
`state`, `history` (full message list, capped at 100), `turn_count`, `lead_data`.
In `llm.py build_context`:

- **≤ 10 messages:** the full raw history is injected into the prompt.
- **> 10 messages:** older turns are collapsed into a single **summary blob**
  (cached at `summary:{lead_id}`), and only the last 10 raw turns are kept.

`bant.py` separately extracts BANT signals in the background, and the
qualification gate reads `{{scoring_status}}`.

**Problem.** The summarization is lossy and generic. In long conversations it
can drop the specifics that matter for a sales rep: the lead's company, the
exact pain in their own words, BANT signals, objections already raised, and
whether they already agreed to book. Symptoms: Mark "forgetting" earlier
details, weaker/inconsistent qualification, and re-asking things (which the
prompt's intent-dedup rules try to prevent but can't guarantee once the fact
has fallen out of context). We already reach the > 10-message path in testing,
so this is a live quality issue, not hypothetical.

Industry pattern for agent memory (2025-2026): **inject → distill → trim →
consolidate** — maintain a compact, structured memory that is updated each turn
rather than relying on re-summarizing raw history. (Sources reviewed; content
rephrased for licensing compliance.)

### Constraints
- Low volume today; must not add heavy per-turn cost/latency unnecessarily.
- Must slot into the layered context from ADR 0001 (as the "Memory" layer).
- Must be reversible/toggleable (same discipline as `USE_LAYERED_CONTEXT`).
- Existing sessions must keep working (schema-additive, defaulted).

---

## Options Considered

### Option A — Keep raw-history + summary blob (do nothing)
- **Description:** Leave current memory as-is.
- **3-year TCO:** Zero build; ongoing quality cost (forgetting, weak qualification) that grows with conversation length.
- **Risk:** Low technical, **Medium-High product** (persona consistency, lost qualification signals).

### Option B — Structured lead-memory record (CHOSEN)
- **Description:** Add a compact, structured `lead_memory` object to the session, updated each turn and injected as the Memory layer. Keep the last N raw turns as messages; drop the lossy full-history summary in favour of the structured record plus a short rolling summary.
  ```
  lead_memory = {
    "name", "company", "industry",
    "lead_source", "volume",
    "pains": [...], "objections_raised": [...],
    "ai_attitude", "commitments": [...],
    "booking_status", "notes"
  }
  ```
  Pattern: **inject** the record → **distill** new facts from each turn (cheap Fireworks call or rules; can run async/background like BANT) → **trim** raw turns to a small window → **consolidate** into the record + a short rolling summary.
- **3-year TCO:** Moderate build (a few engineering days); small ongoing cost (one cheap distill step per turn, can be async). Net token reduction vs re-injecting long history.
- **Risk:** Medium build risk (session schema + distill accuracy) → mitigated by the flag + eval cases.

### Option C — External vector/long-term memory store (e.g. Pinecone) for full memory
- **Description:** Store all turns as vectors and retrieve relevant memories per turn.
- **3-year TCO:** Higher build + a per-turn retrieval call; adds a datastore.
- **Risk:** Overkill for single-conversation memory at this scale. Better suited to *cross-conversation* / long-term memory later. (Pinecone is already earmarked for KB grounding in ADR 0001 Phase 4, not per-conversation memory.)

---

## Decision

Adopt **Option B — Structured lead-memory record**, implemented as the "Memory"
layer of the ADR 0001 layered context.

- Extend the session with `lead_memory` (schema-additive, defaulted for existing sessions).
- Add a **distill** step (extend/reuse the existing `bant.py` extraction) that updates `lead_memory` from each turn; run it in the background to avoid adding reply latency.
- `build_context` injects a compact `lead_memory` block (a few hundred tokens) and the last N raw turns; retire reliance on the lossy full-history summary.
- Gate behind a flag (e.g. `USE_STRUCTURED_MEMORY`, default off until validated) for instant revert, mirroring `USE_LAYERED_CONTEXT`.
- Defer cross-conversation / vector long-term memory (Option C) — out of scope here.

---

## Consequences

**Easier:**
- Mark reliably recalls company, pain (in the lead's words), objections, and booking status across long threads → consistent persona.
- Qualification gate (`scoring_status`) becomes reliable because the 3 signals persist in structured form.
- Fewer tokens than re-injecting long raw history.
- Formalizes what `bant.py` already does into a first-class, reusable layer.

**Harder / trade-offs:**
- Adds a per-turn distill step (LLM cost/latency) — mitigated by running async/background and/or rules-first.
- Session schema grows; needs safe defaults + migration tolerance.
- Distill accuracy must be evaluated (wrong facts in memory are worse than none) → requires eval cases.
- One more moving part in the conversation engine.

**Sequencing & guardrails:**
- Build **after** Phase 2 (layered persona) is validated and the Phase 4 grounding direction (Pinecone vs Supabase pgvector) is decided — no point tuning memory before persona/facts are settled.
- Ship behind `USE_STRUCTURED_MEMORY` flag.
- Add eval cases to the harness (ADR 0002): after 15+ turns Mark still recalls company/pain; qualification signals persist; no re-asking of answered questions.

**Success criteria:**
- No "forgetting" of company/pain/booking status in long test conversations.
- Qualification decisions consistent with the structured signals.
- Per-reply token count stays at or below the Phase 2 layered baseline.

---

## Implementation (shipped)

- `app/lead_memory.py` — pure `merge_memory()` (never wipes/invents facts) + `format_memory_block()` (compact, only prints known fields, no em dashes) + `distill_and_update()` (cheap background LLM distill via the Fireworks router, best-effort, re-reads session before write to avoid clobbering concurrent writes).
- `app/config.py` — `USE_STRUCTURED_MEMORY: bool = False` flag (Heroku-flippable, no redeploy).
- `app/llm.py build_context` — injects the memory block as a system message; when memory is present it **skips the lossy summary blob** and keeps only the recent raw turns. Guarded so any failure falls through to legacy behavior.
- `app/graph/nodes.py persist_session_node` — fires `distill_and_update` as a background task next to BANT; instant no-op when the flag is off.
- `tests/test_lead_memory.py` — 12 eval cases proving merge safety (empty delta never wipes, list union/dedup, string coercion, format output). All green.

**Rollout:** deploy with flag off (zero behavior change), then set `USE_STRUCTURED_MEMORY=true` in Heroku config vars to enable live; set back to `false` to revert instantly.
