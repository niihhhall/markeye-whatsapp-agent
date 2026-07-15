# ADR 0004 — Grounding (Retrieval) + Deterministic Hallucination Filter

- **Status:** Accepted (Option B + B2). B2 shipped live; grounding shipped behind USE_PINECONE_GROUNDING (default off). See Implementation section.
- **Date:** 2026-07-15
- **Owner:** Engineering (CTO advisory)
- **Deciders:** Founder, Engineering
- **Related:** ADR 0001 (Layered Context — this is its Phase 4 "grounding" layer), ADR 0002 (Eval Harness), ADR 0003 (Structured Memory)

---

## Context

This is **Phase 4** of the ADR 0001 roadmap, the final layer. Phases 1-3 are
shipped: trimmed prompt, layered/selective context, and structured lead memory.
What remains is **grounding** (making Mark answer factual questions from a real
source of truth) and a **deterministic hallucination filter** (a code-level
guard against specific false claims).

### Where grounding stands today

There are effectively **two** fact sources in the running system:

1. **`prompts/layers/knowledge.md`** — a static block injected only when the
   lead's message hits KB keywords (ADR 0001 Phase 2). This works and is live.
2. **A pgvector RAG scaffold in Supabase** — `app/knowledge.py::retrieve_knowledge`
   calls a `match_knowledge` RPC using **OpenAI `text-embedding-3-small`**
   embeddings. **This path is currently dead**: OpenAI embeddings are switched
   off (no key / 401 by design), so `retrieve_knowledge` silently returns `""`
   on every call. The wiring exists; the embedding source does not.

So today Mark's factual grounding is **prompt-only** (`knowledge.md` +
the "Truth Boundary" instruction in `facts.md` that says "if you don't know it,
push to the call"). That is a reasonable floor, but:

- It does not scale as the knowledge base grows (a static block can't hold a
  large, changing FAQ / pricing / integrations catalogue).
- Prompt-only truth control is the **weakest** guardrail; under pressure the
  model can still invent a plausible-sounding answer (a fake integration, a
  made-up price, an imagined case study).

### Where hallucination control stands today

`app/output_guard.py` already does deterministic outbound sanitisation for the
dash/emoji rules (ADR: "never send —"). It is the proven pattern for a
**code-level** guard that runs on every outgoing message. It does **not** yet
check for banned factual claims.

### Constraints
- Fireworks is the only LLM provider. **OpenAI embeddings stay off** (prior decision).
- Low volume today; must not add heavy per-turn cost/latency or a fragile new dependency without payoff.
- Must slot into the layered context (grounding = the "Facts via retrieval" layer of ADR 0001).
- Reversible/toggleable, same discipline as `USE_LAYERED_CONTEXT` / `USE_STRUCTURED_MEMORY`.
- A free **Pinecone** account is already earmarked for this (integrated `llama-text-embed-v2`, no OpenAI needed).

---

## Two workstreams

Phase 4 is really two independent deliverables. They can ship separately.

### Workstream A — Grounding (retrieval)
Give Mark a real, searchable source of truth so factual answers are retrieved,
not guessed. Requires an embedding source (not OpenAI) + a vector store.

### Workstream B — Deterministic banned-claims output filter
Extend `output_guard.py` to hard-block a known list of false/forbidden claims
(fake case studies, unapproved pricing, promises not offered) at the code level,
regardless of what the model generated. Cheap, high-safety, no new dependency.

---

## Options Considered (Workstream A — grounding)

### Option A — Keep prompt-only grounding (do nothing)
- **Description:** Leave `knowledge.md` + Truth Boundary as the only fact source; leave the dead OpenAI pgvector path as-is.
- **3-year TCO:** Zero build; grounding quality caps out and every KB change means editing the prompt file.
- **Risk:** Low technical, **Medium product** (hallucination risk grows with KB size; no scalable fact store).

### Option B — Pinecone with integrated embeddings (RECOMMENDED)
- **Description:** Store the knowledge base as vectors in Pinecone using its built-in `llama-text-embed-v2` (Pinecone does the embedding server-side, so **no OpenAI, no separate embed call in our code**). Rewrite `retrieve_knowledge` to query Pinecone; keep the same "inject retrieved facts into `build_context`" contract already wired in Phase 2. Gate behind `USE_PINECONE_GROUNDING`.
- **3-year TCO:** Low-moderate build (index setup + ingest script + swap the retrieval call); free tier covers current volume; one managed dependency.
- **Risk:** Medium (new datastore + an ingest/refresh step to keep the index in sync with the KB). Mitigated by flag + fallback to `knowledge.md` on any Pinecone error.
- **Why recommended:** Matches the earmarked account, needs no OpenAI, and integrated embeddings mean the least code (Pinecone embeds both the documents and the query for us).

### Option C — Supabase pgvector + a non-OpenAI embedding model
- **Description:** Reuse the **existing** `match_knowledge` pgvector RPC in Supabase (already built), but replace the embedding source with a non-OpenAI model, e.g. a Fireworks embedding model or an open model. No new datastore.
- **3-year TCO:** Low-moderate build; **zero new infra** (Supabase already in the stack); we own the embedding call.
- **Risk:** Medium (must run our own embed call for both ingest and query; must confirm a suitable embedding model + re-create the index at the right dimension).
- **Why not default:** Slightly more code than B (we manage embedding), and it doesn't use the earmarked Pinecone account. But it keeps everything in one datastore, so it's the strongest alternative if you'd rather not add Pinecone.

---

## Options Considered (Workstream B — output filter)

### Option B1 — Prompt-only truth control (status quo)
- Weakest guarantee; relies on the model obeying instructions.

### Option B2 — Deterministic banned-claims filter (RECOMMENDED)
- **Description:** Extend `output_guard.check_reply` / `sanitize_outgoing` with a configurable list of banned claim patterns (regex/keywords for fake pricing, invented case studies, forbidden promises). On a hit: strip/rewrite or fall back to a safe "let's cover that on the call" line, and log it. Same proven pattern as the dash sanitiser.
- **3-year TCO:** Very low build; near-zero runtime cost.
- **Risk:** Low. Main work is curating the banned list with you (what must Mark never claim).

---

## Decision (proposed)

- **Grounding:** Adopt **Option B — Pinecone with integrated embeddings**, gated behind `USE_PINECONE_GROUNDING` (default off), with automatic fallback to the existing `knowledge.md` layer on any error. Keep the current `build_context` retrieval-injection contract; only the retrieval backend changes. (Option C remains the documented fallback if we later prefer to avoid a second datastore.)
- **Output filter:** Adopt **Option B2** and ship it **independently and first**, since it's cheap, safe, and valuable on its own.

Rationale: B uses the earmarked account, needs no OpenAI, and is the least code because Pinecone embeds documents and queries server-side. The filter is a separate, low-risk win that shouldn't wait on the RAG build.

---

## Consequences

**Easier:**
- Mark answers factual questions (integrations, pricing tiers, policies, case studies) from a real, updatable source instead of a static block, less "push to the call" dodging on things we can actually answer.
- Knowledge base becomes data (re-ingest to update) instead of a prompt edit + redeploy.
- The banned-claims filter gives a hard, testable guarantee that specific false claims never go out, independent of the model.
- Grounding + filter complete the ADR 0001 "layered hallucination control": retrieve facts → model → deterministic filter → evals.

**Harder / trade-offs:**
- Pinecone adds a managed dependency and an **ingest/refresh step** (the index must be kept in sync when the KB changes) — needs a small `scripts/ingest_kb.py`.
- Retrieval adds one network call per relevant turn (mitigated: only when the message is KB-relevant, same keyword gate as Phase 2; cached where possible).
- The banned-claims list must be curated and maintained with the founder (a false positive that strips a legitimate answer is its own failure mode) → covered by eval cases.

**Sequencing & guardrails:**
1. **Ship Workstream B (output filter) first** — small, safe, immediate value. Add eval cases (banned claim in → safe line out).
2. Let Phases 2-3 soak on real conversations; confirm `knowledge.md` gaps that actually justify RAG before building it.
3. **Then Workstream A (Pinecone):** create index → `scripts/ingest_kb.py` to embed the KB → swap `retrieve_knowledge` to Pinecone → flag `USE_PINECONE_GROUNDING` → fallback to `knowledge.md` on error → eval cases (known question → grounded answer; unknown → still pushes to call).
4. Secrets via Heroku config vars only (`PINECONE_API_KEY`, index name), never in `.env` committed anywhere.

**Success criteria:**
- Banned claims never appear in outbound messages (filter eval green).
- For KB-answerable questions, Mark gives the correct grounded answer instead of deflecting.
- For unknown questions, Mark still refuses to invent and pushes to the call (Truth Boundary preserved).
- No regression in latency/cost outside the KB-relevant turns; Pinecone failure falls back cleanly to `knowledge.md`.

---

## Not in scope
- Per-conversation memory (done in ADR 0003).
- Cross-conversation / long-term lead memory (future; could reuse the same vector store).
- Replacing the LLM provider or the LangGraph harness.


---

## Implementation (shipped)

**Workstream B2 — banned-claims output filter (LIVE):**
- `app/output_guard.py` — `redact_banned_claims()` (drops the offending sentence, deflects to the call if that empties the reply) + `guard_outgoing()` (sanitize + claim enforcement, single production entry point). `SAFE_DEFLECTION` is style-compliant (no dash/emoji/claim).
- `app/message_router.py send_message` — now calls `guard_outgoing()` instead of `sanitize_outgoing()`.
- `app/config.py` — `ENABLE_CLAIM_FILTER: bool = True` (Heroku-flippable).
- `tests/test_output_guard.py` — eval cases: sentence-level redaction, whole-message deflection, compose-with-sanitize, deflection is clean. All green.

**Workstream A — Pinecone grounding (shipped, flag-gated OFF):**
- `app/knowledge.py` — `retrieve_knowledge()` prefers Pinecone (integrated `llama-text-embed-v2`, no OpenAI), falls back to the legacy Supabase path, and returns "" on any error so `build_context` uses the static `knowledge.md` layer. Search call is version-tolerant across SDK generations.
- `app/config.py` — `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_NAMESPACE`, `PINECONE_EMBED_MODEL`, `USE_PINECONE_GROUNDING` (default off).
- `requirements.txt` — `pinecone>=5.1,<7.0`.
- `scripts/ingest_kb.py` — creates the integrated index and ingests `prompts/layers/knowledge.md` + `prompts/knowledge/*.txt` + `prompts/objections/*.txt` as chunked records.

**Rollout for grounding:**
1. Set `PINECONE_API_KEY` in Heroku config vars.
2. Run the ingest once: `heroku run "python scripts/ingest_kb.py" -a mark-ai-sdr-agent` (or locally with the key set).
3. Flip `USE_PINECONE_GROUNDING=true`. Revert by setting it back to `false` (falls back to `knowledge.md`).
