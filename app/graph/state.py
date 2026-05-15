from typing import TypedDict, Optional, List

class GraphState(TypedDict):
    # ── Input params ─────────────────────────────────────
    phone: str
    message: str
    conversation_id: str
    message_id: str
    last_message_ts: float
    client_id: Optional[str]

    # ── Loaded context ────────────────────────────────────
    session: dict
    lead_data: dict
    lead_id: Optional[str]
    client_config: Optional[dict]

    # ── Processing ────────────────────────────────────────
    knowledge_context: str
    response_text: str
    tool_calls: List[str]

    # ── Control flow ──────────────────────────────────────
    should_exit: bool
    exit_reason: str
