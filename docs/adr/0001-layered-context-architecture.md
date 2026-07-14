# ADR 0001 — Layered Context Architecture for the AI SDR

- **Status:** Accepted (2026-07-14) — Option B
- **Date:** 2026-07-14
- **Owner:** Engineering (CTO advisory)
- **Deciders:** Founder, Engineering
- **Related:** ADR 0002 (Agent Harness & Eval Harness)

---

## Context

Mark (the AI SDR) currently runs on a **single ~41,000-character system prompt** stored in the Supabase `clients.system_prompt` field and injected **in full on every message**.

Observed problems in production (Heroku, WhatsApp Cloud API + Fireworks):

1. **Persona dilution.** The persona feels generic even though the full prompt is loaded. The prompt is a monolith with duplicated sections (e.g., "WHO YOU ARE" appears twice) and mixes identity, facts, 5-phase playbook, objection handling, and hard rules in one blob. LLMs exhibit recency bias and lose instruction adherence as always-on context grows.
2. **Cost & latency.** Every reply ships ~10,000 input tokens (measured: `tokens_in: 10355`). Replies take ~7s; occasional 30s timeouts and a 6s stage-classifier timeout.
3. **Dead selective-loading paths.** `llm.py` references `prompts/knowledge/*.txt` and `prompts/objections/*.txt` that do not exist, so the code silently dumps the whole prompt regardless of the lead's message.
4. **Weak memory.** After 10 messages the whole history is collapsed into one summary blob, losing structured lead facts (name, company, pains, BANT) that keep the persona consistent.
5. **Prompt-only hallucination control.** The "Truth Boundary / never lie" list is strong but enforced only by instructions — the weakest guardrail layer.

Industry direction (2025-2026): the field has moved from *prompt engineering* (one fixed string) to **context engineering** — dynamically assembling the right context per inference call (system prompt + memory + retrieval + state). (Sources reviewed; content rephrased for licensing compliance.)

### Constraints
- Single-tenant today, but the codebase is explicitly multi-tenant (per-client UUID, per-client WhatsApp number, per-client config).
- Low volume (single Meta test number) — must not over-engineer.
- Keep the existing LangGraph / state-machine architecture; the problem is the **context layer**, not the graph.

---

## Options Considered

### Option A — Keep the monolith prompt (do nothing)
- **Description:** Leave the 41k-char prompt as-is in the DB.
- **3-year TCO:** Low build cost; **high running cost** (~10k tokens/reply forever) + ongoing persona/latency complaints.
- **Risk:** Low technical risk, **High product risk** (persona quality, cost creep, timeouts).

### Option B — Layered, selectively-loaded context (CHOSEN)
- **Description:** Split the prompt into modular layers, assembled per turn, loading only what's relevant:
  1. **Identity / persona** (`soul.md`-style) — short, always in prompt slot #1.
  2. **Facts / grounding** — confirmed facts + "never lie" list; retrieved, not always-on.
  3. **Playbook** — 5-phase guidance + objection handling; load only the current phase + matched objection.
  4. **Memory** — structured lead record + rolling summary (inject → distill → trim → consolidate).
  5. **Examples** — few-shot reference conversations selected by similarity.
- **3-year TCO:** Moderate build (a few engineering days); **materially lower running cost** (~2-3k tokens/reply vs ~10k).
- **Risk:** Medium build risk (must not regress persona → mitigated by the eval harness in ADR 0002).

### Option C — Rewrite onto a different agent framework / "soul.md" product
- **Description:** Replace LangGraph with a single-agent assistant framework (OpenClaw/Hermes-style) built around `soul.md`.
- **3-year TCO:** High build cost (full rewrite); throws away working state-machine/funnel logic.
- **Risk:** **High.** Those frameworks target single autonomous assistants, not stateful multi-tenant sales funnels. Wrong tool for the job.

---

## Decision

Adopt **Option B — Layered Context Architecture.**

Split the monolithic prompt into modular files as the source of truth, and change `build_context` to **assemble a focused, per-turn context** (identity always; facts/playbook/examples selectively). Keep the LangGraph harness. For multi-tenancy, layers can be composed per client (DB stores overrides; files provide defaults/templates).

Adopt the **principle** behind `soul.md` (a short, dedicated identity block in prompt slot #1) — not the product. Files are the source; the runtime still assembles one focused string per turn.

Hallucination becomes **layered**: (1) grounding via retrieval of relevant facts, (2) a deterministic output filter for the known banned claims (same pattern as the shipped dash sanitizer), (3) evals (ADR 0002).

---

## Consequences

**Easier:**
- Sharper, more on-character replies (persona no longer competes with 40k chars for attention).
- ~60-70% lower input tokens per reply → lower Fireworks cost and lower latency.
- Maintainable prompt (edit small files, not a 41k blob); reviewable in git.
- Per-client persona customization becomes clean.

**Harder / trade-offs:**
- More moving parts in `build_context` (assembly logic, selection rules).
- **Drift risk** between the source files and the DB copy — must pick one source of truth or add a sync step.
- Requires the eval harness (ADR 0002) to safely change the prompt without regressing persona.
- Retrieval/grounding for facts adds an embedding/store dependency (can be phased; start with static selection before RAG).

**Migration path (phased, reversible):**
- **Phase 1:** Trim + de-duplicate the current prompt; short identity block first; bump classifier timeout; fix booking link. (Low risk.)
- **Phase 2:** Split into modular files (`soul.md`, `facts.md`, `playbook/`, `style.md`) + wire selective loading (current phase + matched objection only).
- **Phase 3:** Structured memory (lead record + rolling summary); deploy the `conversations/` examples so few-shot works.
- **Phase 4:** Grounding (facts → retrieval) + deterministic banned-claims output filter.

Each phase is independently deployable and revertible via git.
