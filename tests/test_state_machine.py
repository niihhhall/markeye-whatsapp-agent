from app.state_machine import check_transition
from app.models import ConversationState

def test_opening_to_discovery():
    session = {"turn_count": 1, "bant_scores": {}}
    new_state = check_transition(ConversationState.OPENING, session)
    assert new_state == ConversationState.DISCOVERY

def test_discovery_no_transition_early():
    session = {"turn_count": 2, "bant_scores": {}}
    new_state = check_transition(ConversationState.DISCOVERY, session)
    assert new_state is None

def test_discovery_to_qualification():
    session = {"turn_count": 3, "bant_scores": {}}
    new_state = check_transition(ConversationState.DISCOVERY, session)
    assert new_state == ConversationState.QUALIFICATION

def test_qualification_to_booking():
    session = {"turn_count": 5, "bant_scores": {"overall_score": 7}}
    new_state = check_transition(ConversationState.QUALIFICATION, session)
    assert new_state == ConversationState.BOOKING

def test_qualification_no_transition_low_score():
    session = {"turn_count": 5, "bant_scores": {"overall_score": 6}}
    new_state = check_transition(ConversationState.QUALIFICATION, session)
    assert new_state is None
