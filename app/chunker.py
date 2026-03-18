import re
import random
from app.config import settings

def chunk_message(text: str) -> list[str]:
    """
    Split LLM response into separate WhatsApp messages.
    Priority: ||| markers → [CHUNK] legacy → sentence split → single message.
    HARD CAP: 3 chunks maximum.
    """
    text = text.strip()
    if not text:
        return [text]
    
    # NEW HUMAN-LIKE LOGIC:
    # 1. Look for a natural first sentence (acknowledgment/greeting)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    if len(sentences) > 1:
        first_sent = sentences[0].strip()
        # If first sentence is an acknowledgment (short), make it the first bubble
        if len(first_sent) < 100:
            chunks = [first_sent, " ".join(sentences[1:]).strip()]
        else:
            # Fallback to standard splitting
            chunks = _split_at_sentences(text)
    else:
        chunks = [text]

    # Clean up chunks
    chunks = [c.strip() for c in chunks if c.strip()]

    # HARD CAP: 3 chunks maximum
    if len(chunks) > 3:
        chunks = chunks[:2] + [" ".join(chunks[2:])]
    
    return chunks or [text]


def _split_at_sentences(text: str) -> list[str]:
    """Force split into 2 chunks if long enough, otherwise keep together."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if len(text) < 400 or len(sentences) <= 1:
        return [text.strip()]
        
    mid = len(sentences) // 2
    return [" ".join(sentences[:mid]), " ".join(sentences[mid:])]


def calculate_typing_delay(text: str) -> float:
    """Calculate delay based on typing speed (approx 250 characters per minute)."""
    # 250 CPM = 4.16 chars per second -> ~0.24s per char
    # We'll use a slightly faster but variable baseline
    words = len(text.split())
    base = words * 0.4  # Approx 0.4s per word
    char_base = len(text) * 0.05
    
    total = (base + char_base) / 2
    jitter = random.uniform(-0.5, 0.5)
    
    return max(1.5, min(5.0, total + jitter))
