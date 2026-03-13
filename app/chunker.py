import re
import random

def chunk_message(text: str) -> list:
    """
    Cleans text and splits into multiple bubbles based on natural pauses (. ! ?).
    Enforces 'No Dashes' rule.
    """
    if not text:
        return []

    # 1. Enforce 'No Dashes' rule
    text = re.sub(r'(\d+)\s*[-—]\s*(\d+)', r'\1 to \2', text)
    text = text.replace("—", ",").replace("--", ",").replace("- ", ", ").replace(" -", " ,")
    
    # 2. Cleanup separators
    text = text.replace("|||", " ")
    
    # 3. Natural Sentence Splitting
    # We split on . ! or ? followed by a space, but protect common abbreviations
    # Common abbreviations to NOT split on
    abbreviations = ["Mr.", "Mrs.", "Dr.", "Ms.", "e.g.", "i.e.", "vs.", "etc.", "st.", "ave."]
    
    # Protect abbreviations by temporary replacement
    protected_text = text
    for i, abbr in enumerate(abbreviations):
        protected_text = protected_text.replace(abbr, f"__ABBR{i}__")
    
    # Split using regex: look for . ! or ? followed by space (or end of string)
    chunks = re.split(r'(?<=[.!?])\s+', protected_text)
    
    # Restore abbreviations and clean up
    temp_chunks = []
    for chunk in chunks:
        c = chunk.strip()
        if not c:
            continue
        for i, abbr in enumerate(abbreviations):
            c = c.replace(f"__ABBR{i}__", abbr)
        temp_chunks.append(c)
    
    # 4. Smart Merge: Combine chunks shorter than 40 chars with the previous one
    merged_chunks = []
    for chunk in temp_chunks:
        if merged_chunks and len(merged_chunks[-1]) < 40:
            # If previous was too short, merge CURRENT into it
            merged_chunks[-1] = merged_chunks[-1] + " " + chunk
        elif merged_chunks and len(chunk) < 30:
            # If current is very short, merge into PREVIOUS
            merged_chunks[-1] = merged_chunks[-1] + " " + chunk
        else:
            merged_chunks.append(chunk)

    # 5. Bubble Cap: Maximum 3 bubbles
    if len(merged_chunks) > 3:
        # Keep first two bubbles as is
        final_list = merged_chunks[:2]
        # Combine everything else into the 3rd bubble
        remaining = " ".join(merged_chunks[2:])
        final_list.append(remaining)
        return final_list
    
    return merged_chunks if merged_chunks else [text.strip()]


def calculate_typing_delay(text: str) -> float:
    """
    Returns a realistic typing delay (in seconds) based on character count.
    Used for simulating a human typing on WhatsApp.
    """
    # 15 chars per second (quite fast but human)
    delay = len(text) / 15.0
    # Cap delay at 15 seconds total for the single coherent response
    return min(max(2.0, delay), 15.0)

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
