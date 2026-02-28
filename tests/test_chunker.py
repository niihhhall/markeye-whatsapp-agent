from app.chunker import chunk_message, calculate_typing_delay

def test_short_message():
    text = "Hello, how are you?"
    chunks = chunk_message(text)
    assert len(chunks) == 1
    assert chunks[0] == text

def test_chunk_marker():
    text = "First thought. [CHUNK] Second thought."
    chunks = chunk_message(text)
    assert len(chunks) == 2
    assert chunks[0] == "First thought."
    assert chunks[1] == "Second thought."

def test_long_message_auto_split():
    text = "This is a very long message that should be split into multiple chunks because it exceeds the maximum character limit for a single WhatsApp message. It contains several sentences to allow for natural breakpoints."
    chunks = chunk_message(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 160

def test_typing_delay():
    delay = calculate_typing_delay("Small text")
    assert 1.0 <= delay <= 4.0
    
    delay_long = calculate_typing_delay("A very very long text that should take more time to type naturally on a mobile keyboard during a simulated conversation.")
    assert delay_long > delay
