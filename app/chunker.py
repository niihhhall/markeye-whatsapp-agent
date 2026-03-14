import re
import random
from app.config import settings

def chunk_message(text: str) -> list[str]:
    """
    Split LLM response into separate WhatsApp messages.
    
    Priority:
    1. ||| separators (LLM outputs these)
    2. [CHUNK] markers (legacy)
    3. Short = single message
    4. Long without markers = split at sentences
    
    HARD CAP: 3 chunks maximum. Always.
    """
    text = text.strip()
    if not text:
        return [text]
    
    # Enforce 'No Dashes' rule broadly
    text = re.sub(r'(\d+)\s*[-—]\s*(\d+)', r'\1 to \2', text)
    text = text.replace("—", ",").replace("--", ",").replace("- ", ", ").replace(" -", " ,")
    
    chunks = None
    
    if "|||" in text:
        chunks = [c.strip() for c in text.split("|||") if c.strip()]
    elif "[CHUNK]" in text:
        chunks = [c.strip() for c in text.split("[CHUNK]") if c.strip()]
    else:
        # Check if we can split sentences naturally regardless of length
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
        if len(sentences) > 1:
            chunks = _split_at_sentences(sentences)
        elif len(text) <= 200:
            return [text]
        else:
            chunks = _split_at_sentences(sentences)
    
    if not chunks:
        return [text]
    
    # HARD CAP: 3 max
    if len(chunks) > 3:
        chunks = chunks[:2] + [" ".join(chunks[2:])]
    
    chunks = [c for c in chunks if c.strip()]
    return chunks if chunks else [text]

def _split_at_sentences(sentences: list[str]) -> list[str]:
    if not sentences:
        return []
    
    # If 1, 2, or 3 sentences, just send them as individual messages
    if len(sentences) <= 3:
        return sentences
    
    # If 4 or 5 sentences, split in half
    if len(sentences) <= 5:
        mid = len(sentences) // 2
        return [" ".join(sentences[:mid]), " ".join(sentences[mid:])]
    
    third = len(sentences) // 3
    return [
        " ".join(sentences[:third]),
        " ".join(sentences[third:third*2]),
        " ".join(sentences[third*2:])
    ]

def calculate_typing_delay(text: str) -> float:
    """Realistic typing delay. 2.0s to 5.0s based on length."""
    # Slower typing speed and higher baseline for realism
    base = len(text) * getattr(settings, 'TYPING_DELAY_PER_CHAR', 0.04)
    jitter = random.uniform(-0.2, 0.6)
    return max(2.0, min(5.0, base + jitter))

def calculate_reading_delay(text: str) -> float:
    """
    Returns a realistic reading delay based on incoming message length.
    Avg human reads ~25 chars per second.
    """
    if not text:
        return 1.0
    delay = len(text) / 25.0
    return max(1.0, delay)

def calculate_thinking_delay() -> float:
    """
    Returns a random thinking delay for legacy support, 
    but we now prefer reading_delay + typing_delay.
    """
    return random.uniform(2.0, 4.0)
