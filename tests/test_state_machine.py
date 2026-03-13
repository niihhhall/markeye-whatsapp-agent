"""
Tests for app/state_machine.py — verifies conversation state transitions.
"""
from app.state_machine import check_transition
from app.models import ConversationState


def test_opening_to_discovery():
    """After 1st turn, should move from OPENING to DISCOVERY."""
    session = {"turn_count": 1, "bant_scores": {}}
    new_state = check_transition(ConversationState.OPENING, session)
    assert new_state == ConversationState.DISCOVERY


def test_discovery_stays_early():
    """At turn 2, DISCOVERY should remain (not enough turns)."""
    session = {"turn_count": 2, "bant_scores": {}}
    new_state = check_transition(ConversationState.DISCOVERY, session)
    assert new_state is None


def test_discovery_to_qualification():
    """After 3+ turns, DISCOVERY should promote to QUALIFICATION."""
    session = {"turn_count": 3, "bant_scores": {}}
    new_state = check_transition(ConversationState.DISCOVERY, session)
    assert new_state == ConversationState.QUALIFICATION


def test_qualification_to_booking_high_score():
    """High BANT score (7+) -> BOOKING."""
    session = {"turn_count": 5, "bant_scores": {"overall_score": 7}}
    new_state = check_transition(ConversationState.QUALIFICATION, session)
    assert new_state == ConversationState.BOOKING


def test_qualification_stays_low_score():
    """Low BANT score (< 7) -> stay in QUALIFICATION."""
    session = {"turn_count": 5, "bant_scores": {"overall_score": 6}}
    new_state = check_transition(ConversationState.QUALIFICATION, session)
    assert new_state is None


def test_waiting_stays_waiting():
    """WAITING state should not auto-transition."""
    session = {"turn_count": 10, "bant_scores": {}}
    new_state = check_transition(ConversationState.WAITING, session)
    assert new_state is None


def test_closed_stays_closed():
    """CLOSED state should not auto-transition."""
    session = {"turn_count": 10, "bant_scores": {}}
    new_state = check_transition(ConversationState.CLOSED, session)
    assert new_state is None


if __name__ == "__main__":
    test_opening_to_discovery()
    test_discovery_stays_early()
    test_discovery_to_qualification()
    test_qualification_to_booking_high_score()
    test_qualification_stays_low_score()
    test_waiting_stays_waiting()
    test_closed_stays_closed()
    print("✅ All state machine tests passed!")
