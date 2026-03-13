"""
Tests for app/chunker.py — ensures message cleaning, single-bubble delivery,
and correct typing/reading delay calculations.
"""
from app.chunker import chunk_message, calculate_typing_delay, calculate_reading_delay


def test_returns_single_bubble():
    """chunk_message should always return a single consolidated bubble."""
    text = "Hello, this is a complete message from Albert."
    chunks = chunk_message(text)
    assert len(chunks) == 1


def test_pipe_separators_removed():
    """If LLM outputs ||| separators, they should be cleaned into single bubble."""
    text = "First thought. ||| Second thought. ||| Third thought."
    chunks = chunk_message(text)
    assert len(chunks) == 1
    assert "|||" not in chunks[0]


def test_dash_removal():
    """Dashes should be cleaned from output."""
    text = "Available between 9am - 5pm"
    chunks = chunk_message(text)
    assert "-" not in chunks[0] or "9" not in chunks[0].split("-")[0]


def test_empty_text_returns_empty():
    chunks = chunk_message("")
    assert chunks == []


def test_typing_delay_minimum():
    """Short messages should have at least 2 seconds delay."""
    delay = calculate_typing_delay("Hi")
    assert delay >= 2.0


def test_typing_delay_maximum():
    """Very long messages should not exceed 15 seconds."""
    delay = calculate_typing_delay("A" * 5000)
    assert delay <= 15.0


def test_typing_delay_scales():
    """Longer text should take more time to type."""
    short = calculate_typing_delay("Ok")
    long = calculate_typing_delay("This is a much longer message that would take more time to type.")
    assert long > short


def test_reading_delay_minimum():
    """Short messages should have at least 1 second reading time."""
    delay = calculate_reading_delay("Short")
    assert delay >= 1.0


def test_reading_delay_scales():
    """Longer messages take more time to read."""
    short_delay = calculate_reading_delay("Hi")
    long_delay = calculate_reading_delay("Long " * 100)
    assert long_delay > short_delay


if __name__ == "__main__":
    test_returns_single_bubble()
    test_pipe_separators_removed()
    test_dash_removal()
    test_empty_text_returns_empty()
    test_typing_delay_minimum()
    test_typing_delay_maximum()
    test_typing_delay_scales()
    test_reading_delay_minimum()
    test_reading_delay_scales()
    print("✅ All chunker tests passed!")
