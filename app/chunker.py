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
    
    # 1. Priority: Explicit markers
    if "|||" in text:
        chunks = [c.strip() for c in text.split("|||") if c.strip()]
    elif "[CHUNK]" in text:
        chunks = [c.strip() for c in text.split("[CHUNK]") if c.strip()]
    else:
        # 2. Human logic: Split at every punctuation (., ?, !)
        # But try to keep chunks reasonably sized (don't split "Hi. How are you?" into two if it's too tiny)
        raw_chunks = re.split(r'(?<=[.!?])\s+', text)
        chunks = []
        current_chunk = ""
        
        for rc in raw_chunks:
            rc = rc.strip()
            if not rc: continue
            
            # If current_chunk is empty, start it
            if not current_chunk:
                current_chunk = rc
            # If current_chunk is already "full enough" (e.g. > 100-150 chars), start new bubble
            # OR if it ends with ? or ! (highly prioritized split points for impact)
            elif len(current_chunk) > 120 or current_chunk.endswith('?') or current_chunk.endswith('!'):
                chunks.append(current_chunk)
                current_chunk = rc
            # Otherwise, merge short sentences together (human behavior)
            else:
                current_chunk += f" {rc}"
        
        if current_chunk:
            chunks.append(current_chunk)

    # Clean up chunks
    chunks = [c.strip() for c in chunks if c.strip()]

    # HARD CAP: 5 chunks maximum (user wants more bubbles)
    if len(chunks) > 5:
        chunks = chunks[:4] + [" ".join(chunks[4:])]
    
    return chunks or [text]


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
