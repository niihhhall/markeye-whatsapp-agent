"""
tests/test_lead_memory.py
=========================
ADR 0003 Phase 3 — eval cases for the structured lead-memory logic.

Covers the pure, deterministic parts (merge + format). The LLM distill step is
I/O and is exercised in integration, not here. These tests are the guardrail
against the ADR's core risk: "wrong facts in memory are worse than none" — so
we prove that merging never wipes known facts and never invents them.
"""

from app import lead_memory as lm


def test_default_memory_shape():
    mem = lm.default_memory()
    assert mem["name"] == ""
    assert mem["pains"] == []
    assert mem["objections_raised"] == []
    assert mem["commitments"] == []
    assert lm.is_empty(mem)


def test_merge_fills_scalars():
    out = lm.merge_memory(lm.default_memory(), {"name": "Nihal", "company": "Markeye"})
    assert out["name"] == "Nihal"
    assert out["company"] == "Markeye"
    assert not lm.is_empty(out)


def test_empty_delta_never_wipes_known_facts():
    existing = lm.merge_memory(lm.default_memory(), {"name": "Nihal", "company": "Markeye"})
    # A distill that returns blanks must not erase what we already know.
    out = lm.merge_memory(existing, {"name": "", "company": ""})
    assert out["name"] == "Nihal"
    assert out["company"] == "Markeye"


def test_scalar_update_overwrites_with_nonempty():
    existing = lm.merge_memory(lm.default_memory(), {"booking_status": "interested"})
    out = lm.merge_memory(existing, {"booking_status": "agreed"})
    assert out["booking_status"] == "agreed"


def test_lists_union_and_dedup_case_insensitive():
    existing = lm.merge_memory(lm.default_memory(), {"pains": ["missing leads after hours"]})
    out = lm.merge_memory(existing, {"pains": ["Missing leads after hours", "slow follow-up"]})
    assert out["pains"] == ["missing leads after hours", "slow follow-up"]


def test_lists_accumulate_across_turns():
    m = lm.default_memory()
    m = lm.merge_memory(m, {"objections_raised": ["too expensive"]})
    m = lm.merge_memory(m, {"objections_raised": ["worried AI will make mistakes"]})
    assert "too expensive" in m["objections_raised"]
    assert "worried AI will make mistakes" in m["objections_raised"]


def test_string_coerced_to_list():
    out = lm.merge_memory(lm.default_memory(), {"commitments": "will send details"})
    assert out["commitments"] == ["will send details"]


def test_format_block_empty_when_nothing_known():
    assert lm.format_memory_block(lm.default_memory()) == ""
    assert lm.format_memory_block(None) == ""


def test_format_block_only_prints_known_fields():
    mem = lm.merge_memory(lm.default_memory(), {
        "name": "Nihal",
        "company": "Markeye",
        "pains": ["leads go cold overnight"],
        "booking_status": "agreed",
    })
    block = lm.format_memory_block(mem)
    assert "Nihal" in block
    assert "Markeye" in block
    assert "leads go cold overnight" in block
    assert "agreed" in block
    # Unknown fields must NOT appear as empty noise.
    assert "Industry:" not in block
    assert "Lead source:" not in block


def test_format_block_has_no_em_dash():
    # Project hard rule: never emit an em dash in outward-facing text.
    mem = lm.merge_memory(lm.default_memory(), {"name": "Nihal", "pains": ["x"]})
    assert "\u2014" not in lm.format_memory_block(mem)


def test_seed_then_distill_flow_preserves_everything():
    # Mirrors distill_and_update: seed lead_data, then merge an LLM delta.
    seeded = lm.merge_memory(lm.default_memory(), {"name": "Nihal", "company": "Markeye"})
    delta = {"pains": ["missing after-hours leads"], "booking_status": "interested"}
    final = lm.merge_memory(seeded, delta)
    assert final["name"] == "Nihal"
    assert final["company"] == "Markeye"
    assert final["pains"] == ["missing after-hours leads"]
    assert final["booking_status"] == "interested"


def test_sanitize_lead_name_blocks_agent_name():
    # The agent's own name must never be recorded as the lead's name.
    assert lm.sanitize_lead_name("Mark") == ""
    assert lm.sanitize_lead_name("markeye") == ""
    assert lm.sanitize_lead_name("  MARK  ") == ""
    assert lm.sanitize_lead_name("bot") == ""


def test_sanitize_lead_name_allows_real_names():
    assert lm.sanitize_lead_name("Nihal") == "Nihal"
    assert lm.sanitize_lead_name("Sarah") == "Sarah"
    assert lm.sanitize_lead_name("") == ""
    assert lm.sanitize_lead_name(None) == ""
