"""
Eval harness — layered context assembler (ADR 0001 Phase 2).
Runs locally with pytest, no Redis/Supabase/LLM.

    pytest tests/test_context_assembly.py -v
"""

from app import context_assembler as ca


def test_layers_available():
    # All always-on layer files must exist and be non-empty.
    assert ca.layers_available() is True


def test_always_on_layers_present():
    out = ca.assemble_base_prompt("hey", include_knowledge=False)
    assert "IDENTITY (READ FIRST)" in out          # soul
    assert "TRUTH BOUNDARY" in out                  # facts
    assert "THE FIVE PHASES" in out                 # playbook
    assert "RESPONSE INSTRUCTIONS" in out           # style


def test_identity_is_first():
    out = ca.assemble_base_prompt("hey", include_knowledge=False)
    assert out.lstrip().startswith("═══ IDENTITY")


def test_knowledge_included_when_relevant():
    out = ca.assemble_base_prompt("how much does it cost and do you integrate with hubspot?")
    assert "KNOWLEDGE BASE" in out
    assert ca.knowledge_relevant("what's the pricing") is True


def test_knowledge_omitted_when_irrelevant():
    out = ca.assemble_base_prompt("yeah mate not bad, been a long week")
    assert "KNOWLEDGE BASE" not in out
    assert ca.knowledge_relevant("yeah not bad") is False


def test_style_rules_stay_at_end_after_knowledge():
    out = ca.assemble_base_prompt("tell me about pricing")
    # Response instructions (style) should appear after the knowledge block.
    assert out.index("KNOWLEDGE BASE") < out.index("RESPONSE INSTRUCTIONS")


def test_knowledge_layer_saves_tokens_when_omitted():
    with_kb = ca.assemble_base_prompt("pricing", include_knowledge=True)
    without_kb = ca.assemble_base_prompt("pricing", include_knowledge=False)
    assert len(without_kb) < len(with_kb)
