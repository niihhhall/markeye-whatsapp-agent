import re
from typing import List
from app.config import settings

def chunk_message(text: str) -> List[str]:
    """Splits a message at natural breakpoints."""
    # Split on [CHUNK] markers if present
    if "[CHUNK]" in text:
        chunks = [c.strip() for c in text.split("[CHUNK]") if c.strip()]
        return chunks

    # If message is short, return as single chunk
    if len(text) <= 160:
        return [text]

    # Split at sentence boundaries using regex
    # Matches . ! ? followed by space or end of string
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    chunks = []
    current_chunk = ""
    
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < 160:
            current_chunk += (" " if current_chunk else "") + sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence
            
    if current_chunk:
        chunks.append(current_chunk)
        
    return chunks

def calculate_typing_delay(text: str) -> float:
    """Returns a realistic delay in seconds based on message length."""
    delay = len(text) * settings.TYPING_DELAY_PER_CHAR
    return max(1.0, min(delay, 4.0)) # Min 1s, Max 4s
