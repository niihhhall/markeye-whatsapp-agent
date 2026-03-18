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
    
    chunks = None
    
    if "|||" in text:
        chunks = [c.strip() for c in text.split("|||") if c.strip()]
    elif "[CHUNK]" in text:
        chunks = [c.strip() for c in text.split("[CHUNK]") if c.strip()]
    elif len(text) <= 200:
        return [text]
    else:
        chunks = _split_at_sentences(text)
    
    if not chunks:
        return [text]
    
    # NEW: Merge very short chunks (e.g. "Hello again.") into the next chunk for natural flow
    refined_chunks = []
    i = 0
    while i < len(chunks):
        current = chunks[i].strip()
        # If chunk is very short and not the last one, merge it with the next
        if len(current) < 50 and i < len(chunks) - 1:
            next_chunk = chunks[i+1].strip()
            refined_chunks.append(f"{current} {next_chunk}")
            i += 2  # Skip next since we merged it
        else:
            refined_chunks.append(current)
            i += 1
            
    chunks = [c for c in refined_chunks if c.strip()]

    # HARD CAP: 3 chunks maximum
    if len(chunks) > 3:
        chunks = chunks[:2] + [" ".join(chunks[2:])]
    
    return chunks or [text]


def _split_at_sentences(text: str) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if len(sentences) <= 2:
        return [text.strip()]
    if len(sentences) <= 4:
        mid = len(sentences) // 2
        return [" ".join(sentences[:mid]), " ".join(sentences[mid:])]
    third = len(sentences) // 3
    return [
        " ".join(sentences[:third]),
        " ".join(sentences[third:third*2]),
        " ".join(sentences[third*2:])
    ]


def calculate_typing_delay(text: str) -> float:
    base = len(text) * getattr(settings, 'TYPING_DELAY_PER_CHAR', 0.03)
    jitter = random.uniform(-0.3, 0.3)
    return max(1.0, min(3.5, base + jitter))
