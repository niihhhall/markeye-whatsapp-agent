import re
import random
from app.config import settings


def chunk_message(text: str) -> list[str]:
    """
    Split LLM response into separate WhatsApp messages.
    Priority: ||| markers → [CHUNK] legacy → smart splitting → single message.
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
        # Pre-process for links and "Ps"
        # Split before URL
        url_pattern = r'(https?://\S+)'
        urls = list(re.finditer(url_pattern, text))
        if urls:
            # Only split if the URL isn't already the start of the message
            first_url = urls[0]
            if first_url.start() > 5: # Some buffer
                text = text[:first_url.start()].strip() + "|||" + text[first_url.start():].strip()
                return chunk_message(text)
        # Split before "Ps" or "P.s." or "By the way"
        ps_match = re.search(r'\s+(Ps|P\.s\.|By the way)\s+', text, re.IGNORECASE)
        if ps_match:
            text = text[:ps_match.start()].strip() + "|||" + text[ps_match.start():].strip()
            return chunk_message(text)
        # 2. Fallback: Do not split. Trust the LLM decision.
        # If the LLM didn't use ||| markers, send as single message.
        chunks = [text]

    # Final cleanup and hard cap
    chunks = [c.strip() for c in chunks if c.strip()]
    if len(chunks) > 3:
        chunks = chunks[:2] + [" ".join(chunks[2:])]
    
    return chunks or [text]


def calculate_blue_tick_delay(last_lead_message_time: float, current_time: float) -> float:
    """
    Calculate delay before marking message as read (blue tick).
    - 2 seconds if actively chatting (last message < 60s ago)
    - 5 seconds if returning after a gap
    """
    gap = current_time - last_lead_message_time
    
    if gap < 60:
        # Active chat, quick blue tick
        return random.uniform(1.5, 2.5)
    else:
        # Returning after a gap
        return random.uniform(4.5, 5.5)


def calculate_reading_delay(incoming_text: str) -> float:
    """
    Calculate how long Albert takes to read the incoming message.
    - 0.04 seconds per character
    - Minimum 4 seconds, maximum 10 seconds
    """
    char_count = len(incoming_text)
    
    reading_time = char_count * 0.04
    
    return max(4.0, min(10.0, reading_time + random.uniform(-0.3, 0.3)))


def calculate_typing_delay(text: str) -> float:
    """
    Calculate human-like typing delay for Albert's outgoing message.
    - 0.1 seconds per character (fast but not instant)
    - Minimum 3 seconds for short messages
    - Maximum 10 seconds for long messages
    """
    char_count = len(text)
    
    # 0.1s per character = 10 chars per second = 600 CPM
    base_typing = char_count * 0.1
    
    # Short message override (under 20 chars)
    if char_count < 20:
        return max(3.0, min(4.0, base_typing + random.uniform(0.5, 1.0)))
    
    return max(3.0, min(10.0, base_typing + random.uniform(-0.5, 0.5)))


def calculate_think_pause() -> float:
    """
    Pause between finishing reading and starting to type.
    Simulates Albert thinking about what to say.
    """
    return random.uniform(0.8, 1.2)


def calculate_review_pause() -> float:
    """
    Pause between finishing typing and hitting send.
    Simulates Albert reviewing his message before sending.
    """
    return random.uniform(0.3, 0.7)


def calculate_full_sequence(incoming_text: str, outgoing_text: str, last_lead_message_time: float, current_time: float) -> dict:
    """
    Calculate the full timing sequence for a single message reply.
    Returns dict with each step's delay for the caller to execute in order.
    
    Sequence: blue_tick → reading → think_pause → typing → review_pause → send
    """
    return {
        "blue_tick_delay": calculate_blue_tick_delay(last_lead_message_time, current_time),
        "reading_delay": calculate_reading_delay(incoming_text),
        "think_pause": calculate_think_pause(),
        "typing_delay": calculate_typing_delay(outgoing_text),
        "review_pause": calculate_review_pause(),
    }


def calculate_chunk_sequence(incoming_text: str, chunks: list[str], last_lead_message_time: float, current_time: float) -> list[dict]:
    """
    Calculate the full timing sequence for a multi-chunk reply.
    First chunk gets the full sequence (blue tick, reading, thinking, typing, review).
    Subsequent chunks only get typing + review (no re-reading, no blue tick).
    
    Returns list of dicts, one per chunk.
    """
    if not chunks:
        return []
    
    sequences = []
    
    # First chunk gets the full sequence
    sequences.append({
        "blue_tick_delay": calculate_blue_tick_delay(last_lead_message_time, current_time),
        "reading_delay": calculate_reading_delay(incoming_text),
        "think_pause": calculate_think_pause(),
        "typing_delay": calculate_typing_delay(chunks[0]),
        "review_pause": calculate_review_pause(),
    })
    
    # Subsequent chunks only get typing + review (fires immediately after previous send)
    for chunk in chunks[1:]:
        sequences.append({
            "blue_tick_delay": 0,
            "reading_delay": 0,
            "think_pause": 0,
            "typing_delay": calculate_typing_delay(chunk),
            "review_pause": calculate_review_pause(),
        })
    
    return sequences


def should_interrupt(lead_is_typing: bool, lead_sent_new_message: bool, typing_duration: float, max_typing_wait: float = 20.0) -> str:
    """
    Determine whether Albert should interrupt his current response.
    
    Returns:
        - "continue": keep going with current response
        - "pause": lead is typing, wait up to max_typing_wait seconds
        - "reprocess": lead sent a new message, cancel and reprocess
    """
    if lead_sent_new_message:
        return "reprocess"
    
    if lead_is_typing:
        if typing_duration < max_typing_wait:
            return "pause"
        else:
            # They've been typing for over 20s without sending, continue
            return "continue"
    
    return "continue"
