"""
Eval harness — output guardrail regression tests.
See docs/adr/0002-agent-harness-and-eval.md.

Every style/hallucination bug we hit in production is encoded here as a test so
it can never silently recur. Runs locally with `pytest` — no Redis/Supabase/LLM.

    pytest tests/test_output_guard.py -v
"""

from app.output_guard import (
    sanitize_outgoing,
    check_reply,
    find_dashes,
    find_emojis,
    count_questions,
    find_banned_phrases,
    find_banned_claims,
)


# ─── sanitize_outgoing: dashes → commas ─────────────────────────────────────────

def test_em_dash_replaced():
    # Real production leak: "...waste your time though — are you..."
    assert "—" not in sanitize_outgoing("waste your time though — are you around")
    assert sanitize_outgoing("waste your time though — are you") == "waste your time though, are you"


def test_en_dash_replaced():
    assert "–" not in sanitize_outgoing("book a call – 15 min")


def test_double_and_spaced_hyphen_replaced():
    assert sanitize_outgoing("well -- maybe") == "well, maybe"
    assert sanitize_outgoing("yeah - sure") == "yeah, sure"


def test_url_and_hyphenated_words_preserved():
    url = "https://cal.com/markeye/free-discovery-call"
    assert sanitize_outgoing(url) == url
    assert sanitize_outgoing("state-of-the-art setup") == "state-of-the-art setup"


# ─── sanitize_outgoing: emojis stripped ─────────────────────────────────────────

def test_emoji_stripped():
    assert find_emojis("great 🚀") != []
    out = sanitize_outgoing("great 🚀")
    assert find_emojis(out) == []
    assert "great" in out


def test_plain_text_unchanged():
    msg = "yeah we see this all the time, what's the main bottleneck right now"
    assert sanitize_outgoing(msg) == msg


# ─── check_reply: violation detection (for eval assertions) ─────────────────────

def test_clean_reply_has_no_violations():
    assert check_reply("yeah makes sense, what does the business do exactly") == {}


def test_dash_violation_detected():
    assert "dashes" in check_reply("we help — a lot")


def test_emoji_violation_detected():
    assert "emojis" in check_reply("awesome 🔥")


def test_multiple_questions_flagged():
    # Prompt hard-rule: one question per message.
    v = check_reply("what do you do, and how do you handle leads?")
    # one '?' here; construct an explicit two-question case:
    v2 = check_reply("what do you do? how many leads a month?")
    assert "too_many_questions" in v2


def test_single_question_ok():
    assert "too_many_questions" not in check_reply("what does the business do exactly?")


def test_banned_phrase_detected():
    assert "banned_phrases" in check_reply("Let me know if that works")


def test_banned_claim_detected():
    # Anti-hallucination: these are real lies Mark told in production.
    assert "banned_claims" in check_reply("we have case studies on the website")
    assert "banned_claims" in check_reply("we've been building since 2022")


def test_helpers_directly():
    assert count_questions("a? b?") == 2
    assert find_banned_phrases("great question mate") == ["great question"]
    assert find_dashes("a — b") != []
    assert find_banned_claims("data is stored in the uk") == ["stored in the uk"]
