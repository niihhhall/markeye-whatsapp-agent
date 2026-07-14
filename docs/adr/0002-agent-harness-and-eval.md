# ADR 0002 — Agent Harness Hardening & Eval Harness

- **Status:** Accepted (2026-07-14) — Option B; initial eval harness shipped (`tests/test_output_guard.py`, `app/output_guard.py`)
- **Date:** 2026-07-14
- **Owner:** Engineering (CTO advisory)
- **Deciders:** Founder, Engineering
- **Related:** ADR 0001 (Layered Context Architecture)

---

## Context

Industry framing: **Agent = Model + Harness** (Martin Fowler). The *harness* is everything around the model — the control loop, context assembly, tools, memory, guardrails, retries, error handling, stop conditions, and observability. Mitchell Hashimoto's discipline: **every time the agent makes a mistake, engineer a fix so it never makes that specific mistake again.** (Sources reviewed; content rephrased for licensing compliance.)

**We already have a harness** — the LangGraph app is one, and it is ~70% of a production-grade harness:

| Harness component | Where it lives | Status |
|---|---|---|
| Control loop | LangGraph (`load_context → classify → generate → tools → deliver`) + webhook buffer→process | ✅ |
| Context assembly | `llm.py build_context` | 🟡 monolithic (see ADR 0001) |
| Tools / actions | Trigger tags (`[SEND_CALENDLY]`, `[ESCALATE]`), `agent_tools.py` | ✅ |
| Memory | Redis sessions + rolling summary + Supabase leads | 🟡 needs structuring |
| Guardrails | Dash sanitizer, Truth Boundary, rate-limit, dedup, CLOSED-state | 🟡 add banned-claims filter |
| Retries / error handling | Fireworks 3-model fallback, node try/except | 🟡 timeouts to tune |
| Observability | `/metrics`, structured logs, Sentry | 🟡 no per-turn eval trace |
| Stop conditions | Interrupt handling, CLOSED, `[NO_REPLY]` | ✅ |

**The critical gap:** there is **no pre-deploy eval**. Every bug this cycle — the `logger` NameError, the `lead_id` NameError, the em-dash leak, the reply-JID (`@lid`) delivery failure, and the provider-routing 404 cascade — was discovered by **sending live WhatsApp messages in production**, not before deploy. Production traffic is currently the test suite.

### Constraints
- Low volume (single test number) — harness maturity must match scale; avoid over-engineering.
- Fast iteration via `git push → Heroku auto-deploy` must be preserved.

---

## Options Considered

### Option A — Status quo (test in production)
- **Description:** Keep finding regressions via live messages.
- **3-year TCO:** Zero build; **high hidden cost** (every change risks a silent regression a real lead hits).
- **Risk:** High — reputation/lead loss from bad replies; slow, stressful debugging.

### Option B — Minimal eval harness + guardrail hardening (CHOSEN)
- **Description:**
  - **Eval harness:** `tests/eval_conversations.py` — ~6 scripted lead journeys (opening → discovery → qualification → booking, plus objection, off-topic, and abusive-exit cases) with assertions: no dashes/emojis, one question per message, does not state banned claims, advances phases correctly, sends booking link only after a yes. Runs locally and in CI before deploy.
  - **Guardrail hardening:** extend the deterministic dash-sanitizer pattern to the banned-claims list (block known hallucinated facts at output).
  - **Retry/timeout tuning:** raise the 6s stage-classifier timeout; review the 30s LLM timeout + retry policy.
- **3-year TCO:** Low build (1-2 engineering days initial + small ongoing per new case); **saves far more** in avoided production incidents and faster iteration.
- **Risk:** Low. Purely additive; no runtime behavior change from the eval harness itself.

### Option C — Heavy harness (recursive sub-agents / control plane)
- **Description:** Adopt advanced patterns from the research (recursive agent harnesses, multi-agent control planes).
- **3-year TCO:** High build + operational complexity.
- **Risk:** High and unjustified at current scale — those patterns target autonomous multi-step coding agents, not a single-turn conversational funnel.

---

## Decision

Adopt **Option B — Minimal eval harness + guardrail hardening.**

Treat the existing LangGraph app as the harness and harden it with discipline (the Hashimoto principle): the eval harness encodes every past bug as a permanent regression test, so each is fixed **once**. Order of work:

1. **Eval harness first** — so the ADR 0001 prompt trim (and all future changes) are verifiable before deploy.
2. **Banned-claims output filter** — deterministic anti-hallucination, same pattern as the dash sanitizer.
3. **Timeout/retry tuning.**

Explicitly **reject** heavy harness patterns (Option C) until scale justifies them.

---

## Consequences

**Easier:**
- Regressions caught pre-deploy in seconds, locally/CI — not by real leads in production.
- Safe to refactor the prompt/context (ADR 0001) with confidence.
- Every historical bug becomes a permanent, named test — they cannot silently recur.
- Deterministic guardrails make hallucination and style violations enforceable, not hope-based.

**Harder / trade-offs:**
- Small ongoing cost: each new failure mode should add an eval case (this is the intended discipline, not overhead).
- Eval cases that call the real LLM cost a few tokens per run; keep the set small and focused (mock deterministic layers where possible).
- CI wiring is optional initially (can run locally via `git`-time discipline) but recommended as volume grows.

**Success criteria:**
- No production-only bug discoveries after the eval harness exists for the covered paths.
- Change failure rate trends toward the CTO target (< 5%).
- Prompt/context changes (ADR 0001) ship only after evals pass.
