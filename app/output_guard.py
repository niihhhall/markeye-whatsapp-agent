"""
app/output_guard.py
===================
Deterministic output guardrails for Mark's replies — the single source of truth
for "what a valid reply looks like". Used in two places:

  1. Production: `sanitize_outgoing()` is applied to every message in
     message_router.send_message (safe transforms — strip dashes/emojis).
  2. Eval harness: `check_reply()` returns a list of rule violations so the
     test suite can assert Mark's output obeys the prompt's hard rules
     BEFORE anything ships (see docs/adr/0002-agent-harness-and-eval.md).

Why deterministic: the system prompt bans these patterns, but LLMs slip. Every
style/hallucination bug we hit in production (em dash leak, etc.) is encoded
here once so it can never silently recur.
"""

import re
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

# ─── Dash-as-punctuation (bot tell) → comma ─────────────────────────────────────
# Single hyphens inside words/URLs (e.g. "free-discovery-call") are left intact.
_DASH_SUBS = [
    (re.compile(r"\s*—\s*"), ", "),      # em dash
    (re.compile(r"\s*–\s*"), ", "),      # en dash
    (re.compile(r"\s*--+\s*"), ", "),    # double (or more) hyphen used as a dash
    (re.compile(r"\s+-\s+"), ", "),      # spaced hyphen used as a dash
]

# ─── Emoji / pictographs (prompt bans "any emoji whatsoever") ───────────────────
# Targeted at emoji/symbol/dingbat/flag blocks + variation selector + ZWJ.
# Deliberately excludes box-drawing and arrows to avoid touching normal text.
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F000-\U0001F02F\U00002600-\U000026FF"
    "\U00002700-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F\U0000200D]",
    flags=re.UNICODE,
)

_QUESTION_MARK = "?"

# ─── Banned phrases (subset of the prompt's BANNED list — extend freely) ────────
BANNED_PHRASES = [
    "let me know",
    "i appreciate",
    "great question",
    "fantastic question",
    "i'm here to help",
    "i'm here to assist",
    "thanks for reaching out",
    "i completely understand",
    "looking forward to it",
    "feel free to",
]

# ─── Banned CLAIMS (the "things Mark has lied about" list — anti-hallucination) ──
BANNED_CLAIMS = [
    "building since 2022",
    "case studies",
    "current clients",
    "client references",
    "stored in the uk",
    "robust security",
    "failover",
]


def sanitize_outgoing(text: str) -> str:
    """Apply SAFE transforms to an outgoing message (dashes → commas, strip emojis).
    Only removes/replaces bot-tell characters; never rewrites meaning."""
    if not text:
        return text
    for pattern, repl in _DASH_SUBS:
        text = pattern.sub(repl, text)
    text = _EMOJI_RE.sub("", text)
    # Collapse any double spaces introduced by emoji removal.
    text = re.sub(r" {2,}", " ", text)
    return text


def find_dashes(text: str) -> List[str]:
    hits = []
    for pattern, _ in _DASH_SUBS:
        hits += pattern.findall(text or "")
    return hits


def find_emojis(text: str) -> List[str]:
    return _EMOJI_RE.findall(text or "")


def count_questions(text: str) -> int:
    return (text or "").count(_QUESTION_MARK)


def find_banned_phrases(text: str) -> List[str]:
    low = (text or "").lower()
    return [p for p in BANNED_PHRASES if p in low]


def find_banned_claims(text: str) -> List[str]:
    low = (text or "").lower()
    return [c for c in BANNED_CLAIMS if c in low]


def check_reply(text: str, max_questions: int = 1) -> Dict[str, List[str]]:
    """Return a dict of rule violations for a candidate reply. Empty dict = clean.
    Used by the eval harness to assert output quality before deploy."""
    violations: Dict[str, List[str]] = {}

    dashes = find_dashes(text)
    if dashes:
        violations["dashes"] = dashes

    emojis = find_emojis(text)
    if emojis:
        violations["emojis"] = emojis

    q = count_questions(text)
    if q > max_questions:
        violations["too_many_questions"] = [f"{q} question marks (max {max_questions})"]

    phrases = find_banned_phrases(text)
    if phrases:
        violations["banned_phrases"] = phrases

    claims = find_banned_claims(text)
    if claims:
        violations["banned_claims"] = claims

    return violations


def log_violations(phone: str, text: str) -> Dict[str, List[str]]:
    """Non-blocking observability hook for production — logs any violations that
    survive sanitization (e.g. banned claims/phrases we don't auto-rewrite)."""
    v = check_reply(text)
    if v:
        logger.warning("[OutputGuard] Reply to %s has violations: %s", phone, v)
    return v
