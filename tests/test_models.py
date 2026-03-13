"""
Tests for app/models.py — ensures no enum duplicates and models load correctly.
"""
from app.models import ConversationState, SessionData


def test_no_duplicate_enum_members():
    """Ensure ConversationState has no duplicate values (the bug we fixed)."""
    values = [state.value for state in ConversationState]
    assert len(values) == len(set(values)), "Duplicate enum values found!"


def test_all_expected_states_exist():
    """All required states must be present."""
    required = {"opening", "discovery", "qualification", "booking",
                "escalation", "confirmed", "waiting", "closed"}
    actual = {s.value for s in ConversationState}
    assert required == actual, f"Missing states: {required - actual}"


def test_waiting_state_value():
    assert ConversationState.WAITING == "waiting"


def test_closed_state_value():
    assert ConversationState.CLOSED == "closed"


def test_session_data_default_state():
    """New sessions should start in OPENING state."""
    session = SessionData()
    assert session.state == ConversationState.OPENING


def test_session_data_empty_history():
    session = SessionData()
    assert session.history == []


if __name__ == "__main__":
    test_no_duplicate_enum_members()
    test_all_expected_states_exist()
    test_waiting_state_value()
    test_closed_state_value()
    test_session_data_default_state()
    test_session_data_empty_history()
    print("✅ All model tests passed!")
