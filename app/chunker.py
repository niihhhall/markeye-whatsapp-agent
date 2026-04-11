import re
import random
import time
from app.config import settings


def chunk_message(text: str, is_template: bool = False) -> list[str]:
    """
    Split LLM response into separate WhatsApp messages.
    Priority: ||| markers → [CHUNK] legacy → smart splitting → single message.
    HARD CAP: 3 chunks maximum.

    If is_template is True, bypass all chunking and return as-is.
    Template messages (intro) should NEVER be chunked.
    """
    text = text.strip()
    if not text:
        return [text]

    # Template bypass: never chunk the intro template
    if is_template:
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
            if first_url.start() > 5:  # Some buffer
                text = text[:first_url.start()].strip() + "|||" + text[first_url.start():].strip()
                return chunk_message(text)
        # Split before "Ps" or "P.s." or "By the way"
        ps_match = re.search(r'\s+(Ps|P\.s\.|By the way)\s+', text, re.IGNORECASE)
        if ps_match:
            text = text[:ps_match.start()].strip() + "|||" + text[ps_match.start():].strip()
            return chunk_message(text)
        # 2. Smart split on double newlines
        if "\n\n" in text:
            chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
        else:
            # 3. Fallback: Do not split. Trust the LLM decision.
            # If the LLM didn't use ||| markers or double newlines, send as single message.
            chunks = [text]

    # Final cleanup and hard cap
    chunks = [c.strip() for c in chunks if c.strip()]
    if len(chunks) > 3:
        chunks = chunks[:2] + [" ".join(chunks[2:])]

    return chunks or [text]


def format_message(text: str, is_template: bool = False) -> str:
    """
    Format a single message bubble for readability within WhatsApp.
    Adds line breaks between distinct thoughts within one message.
    This is FORMATTING not CHUNKING. The message stays as one bubble.

    If is_template is True, bypass all formatting and return as-is.

    Rules:
    - 1 to 2 sentences pass through untouched
    - 3+ sentences get line breaks between thought groups
    - Questions always get their own line at the end
    - Drop the full stop from the very last sentence
    - Preserve any intentional line breaks from the LLM
    """
    text = text.strip()
    if not text:
        return text

    # Template bypass: never reformat the intro template
    if is_template:
        return text

    # If LLM already included line breaks, preserve them and just clean up
    if "\n" in text:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        # Drop full stop from last line
        if lines and lines[-1].endswith("."):
            lines[-1] = lines[-1][:-1]
        return "\n\n".join(lines)

    # Split into sentences for processing
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # Sub-process long sentences to split run-ons with internal line breaks
    processed_sentences = []
    
    def recursive_split(s: str) -> list[str]:
        if len(s) <= 100 or s.rstrip().endswith("?"):
            return [s]
        
        # Look for logical break points (and, but, so, which, where, or, specifically, especially)
        break_patterns = [
            r'\s+and\s+', r'\s+but\s+', r'\s+so\s+', r'\s+which\s+', 
            r'\s+where\s+', r'\s+or\s+', r',\s+(?:some|sometimes|especially|specifically|including)\s+'
        ]
        for pattern in break_patterns:
            matches = list(re.finditer(pattern, s, re.IGNORECASE))
            if matches:
                # Find the most "central" match to split on
                best_match = min(matches, key=lambda m: abs(m.start() - len(s)/2))
                if best_match.start() > 30 and best_match.start() < len(s) - 30:
                    split_idx = best_match.start()
                    # If splitting on a comma, keep comma with part 1
                    if s[split_idx] == ',':
                        split_idx += 1
                    
                    part1 = s[:split_idx].strip()
                    part2 = s[split_idx:].strip()
                    return recursive_split(part1) + recursive_split(part2)
        return [s]

    for s in sentences:
        processed_sentences.extend(recursive_split(s))
    
    sentences = processed_sentences

    # 2+ sentences/parts: group into short paragraphs with blank lines between
    if len(sentences) >= 2:
        paragraphs = []
        current_para = []

        for i, sentence in enumerate(sentences):
            is_last = (i == len(sentences) - 1)
            is_question = sentence.rstrip().endswith("?")
            
            # If a part is very long, it deserves its own paragraph regardless
            is_very_long = len(sentence) > 80

            # Questions get their own paragraph
            if is_question and current_para:
                paragraphs.append(" ".join(current_para))
                current_para = [sentence]
            elif is_very_long and current_para:
                paragraphs.append(" ".join(current_para))
                current_para = [sentence]
            else:
                current_para.append(sentence)

            # Break into new paragraph every 1-2 parts to keep it scannable
            # If current_para is long or has 2 items, break it
            if (len(current_para) >= 2 or (current_para and len(current_para[0]) > 80)) and not is_last and not is_question:
                paragraphs.append(" ".join(current_para))
                current_para = []

        if current_para:
            paragraphs.append(" ".join(current_para))

        # Join paragraphs with blank line
        result = "\n\n".join(paragraphs)
    else:
        # 1 part: join normally
        result = " ".join(sentences)

    # Drop full stop from the very last character
    if result.endswith("."):
        result = result[:-1]

    return result


def aggregate_messages(buffer: list[str]) -> str:
    """
    Combine multiple incoming messages into one input string.

    When a lead sends multiple messages in quick succession,
    they should be aggregated into one combined input and
    processed as a single LLM call.

    The caller should implement a 5 second silence timer:
    - Message arrives, start 5 second timer
    - If more messages arrive, reset timer to 5 seconds
    - Once 5 seconds of silence passes, call this function
    - Feed the combined result into the LLM as one input
    """
    if not buffer:
        return ""
    return " ".join([msg.strip() for msg in buffer if msg.strip()])


def calculate_blue_tick_delay(last_lead_message_time: float, current_time: float) -> float:
    """
    Calculate delay before marking message as read (blue tick).
    - 2 seconds if actively chatting (last message < 60s ago)
    - 5 seconds if returning after a gap

    IMPORTANT: When multiple messages arrive during aggregation,
    blue tick ALL of them at once as a batch when this delay fires.
    Do not blue tick them one at a time.
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
    Calculate how long Mark takes to read the incoming message.
    - 0.04 seconds per character
    - Minimum 4 seconds, maximum 10 seconds

    IMPORTANT: The LLM call should fire simultaneously with the
    blue tick, meaning it runs in parallel during this reading delay.
    By the time reading delay finishes, the LLM may already be done.
    """
    char_count = len(incoming_text)

    reading_time = char_count * 0.04

    return max(4.0, min(10.0, reading_time + random.uniform(-0.3, 0.3)))


def calculate_typing_delay(text: str) -> float:
    """
    Calculate cosmetic typing duration for Mark's outgoing message.
    - 0.1 seconds per character
    - Minimum 1.5 seconds
    - No maximum cap, duration scales with character count

    This is the TARGET typing duration. How it interacts with LLM latency:

    - If LLM finished before typing starts:
      Typing runs for this full duration, then sends.

    - If LLM is still generating when typing starts:
      Typing indicator stays on while LLM finishes.
      If LLM finishes BEFORE this duration would end:
        typing continues until this duration completes, then sends.
      If LLM finishes AFTER this duration would have ended:
        sends immediately. No extra wait. LLM processing time
        already exceeded what natural typing would have been.
    """
    char_count = len(text)

    # 0.1s per character = 10 chars per second = 600 CPM
    base_typing = char_count * 0.1

    # Short message override (under 20 chars)
    if char_count < 20:
        return max(1.5, base_typing + random.uniform(0.3, 0.7))

    return max(1.5, base_typing + random.uniform(-0.5, 0.5))


def calculate_think_pause() -> float:
    """
    Pause between finishing reading and starting to type.
    Simulates Mark thinking about what to say.
    Only applies to the first chunk. Not between subsequent chunks.
    """
    return random.uniform(0.8, 1.2)


def calculate_review_pause() -> float:
    """
    Pause between finishing typing and hitting send.
    Simulates Mark reviewing his message before sending.
    Only applies to the first chunk. Not between subsequent chunks.
    """
    return random.uniform(0.3, 0.7)


def calculate_full_sequence(incoming_text: str, outgoing_text: str, last_lead_message_time: float, current_time: float) -> dict:
    """
    Calculate the full timing sequence for a single message reply.
    Returns dict with each step's delay for the caller to execute in order.

    The caller should execute these stages in order:

    1. Aggregation: Wait for 5 seconds of silence (handled externally)
    2. Blue tick + LLM call: Fire both simultaneously
    3. Reading delay: Visible pause, LLM generating in parallel
    4. Think pause: 1 second before typing starts
    5. Typing: Cosmetic duration, overlaps with LLM if still generating
    6. Review pause: 0.5 second before sending
    7. Send

    Poll should_interrupt() every 500ms throughout stages 2-7.
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

    First chunk gets the full sequence:
    blue_tick → reading → think_pause → typing → review_pause → send

    Subsequent chunks get ONLY typing delay:
    typing indicator drops for a split second → comes back on →
    types for character-based duration → sends immediately.
    No reading delay, no think pause, no review pause between chunks.

    IMPORTANT: Call should_interrupt() between each chunk.
    If lead sent a new message, cancel remaining chunks and reprocess.
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

    # Subsequent chunks: typing only, no other delays
    for chunk in chunks[1:]:
        sequences.append({
            "blue_tick_delay": 0,
            "reading_delay": 0,
            "think_pause": 0,
            "typing_delay": calculate_typing_delay(chunk),
            "review_pause": 0,
        })

    return sequences


def should_interrupt(
    lead_is_typing: bool,
    lead_sent_new_message: bool,
    typing_start_time: float,
    typing_stop_time: float,
    max_typing_wait: float = 20.0,
    typing_silence_threshold: float = 5.0
) -> str:
    """
    Determine whether Mark should interrupt his current response.
    Poll this every 500ms throughout the ENTIRE response sequence
    (reading delay, think pause, typing, review pause, between chunks).

    Parameters:
        lead_is_typing: Is the lead currently showing a typing indicator
        lead_sent_new_message: Did the lead send a new message
        typing_start_time: Timestamp when lead first started typing (0 if not typing)
        typing_stop_time: Timestamp when lead last showed typing activity (0 if not typing)
        max_typing_wait: Max seconds to wait for lead to finish typing (default 20)
        typing_silence_threshold: Seconds of no typing before resuming (default 5)

    Returns:
        - "reprocess": Lead sent a new message. Cancel everything.
          Add new message to aggregation buffer. Reset 5 second silence
          timer. Go back to stage 1 and reprocess with full conversation
          history including the new message.

        - "pause": Lead is typing. Mark should:
          1. Immediately remove typing indicator
          2. Wait and keep polling. One of three things will happen:
             a. Lead sends a message → next poll returns "reprocess"
             b. Lead stops typing for 5+ seconds → next poll returns "resume"
             c. Lead types for 20+ seconds without sending → returns "resume"

        - "resume": Lead stopped typing without sending, or timed out.
          Mark should resume from where he paused. If typing indicator
          was showing before, turn it back on and continue.

        - "continue": No interruption detected. Keep going with current step.
    """
    current_time = time.time()

    # New message always triggers full reprocess
    if lead_sent_new_message:
        return "reprocess"

    # Lead is actively typing right now
    if lead_is_typing:
        # Check if they've been typing longer than max wait
        if typing_start_time > 0:
            typing_duration = current_time - typing_start_time
            if typing_duration >= max_typing_wait:
                return "resume"
        return "pause"

    # Lead is NOT typing right now but WAS typing recently
    if typing_stop_time > 0:
        silence_since_typing = current_time - typing_stop_time
        if silence_since_typing >= typing_silence_threshold:
            # They stopped typing for 5+ seconds without sending, resume
            return "resume"
        # Still within the 5 second window, keep pausing
        return "pause"

    return "continue"
