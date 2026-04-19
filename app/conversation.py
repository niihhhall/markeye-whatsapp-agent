import logging

logger = logging.getLogger(__name__)


async def process_conversation(
    phone: str,
    message: str,
    conversation_id: str = "",
    message_id: str = "",
    last_message_ts: float = 0,
    client_id: str = None,
):
    """
    Thin delegation wrapper. All orchestration logic lives in app/graph.py.
    """
    try:
        from app.graph import workflow
        from app.redis_client import redis_client

        initial_state = {
            "phone": phone,
            "message": message,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "last_message_ts": last_message_ts,
            "client_id": client_id,
            "session": {},
            "lead_data": {},
            "lead_id": None,
            "client_config": None,
            "knowledge_context": "",
            "response_text": "",
            "tool_calls": [],
            "should_exit": False,
            "exit_reason": "",
        }

        logger.info("[Conversation] Invoking graph for %s: %s...", phone, message[:50])
        await workflow.ainvoke(initial_state)

    except Exception as e:
        logger.critical("[Conversation] CRITICAL ERROR for %s: %s", phone, e, exc_info=True)
        from app.redis_client import redis_client
        await redis_client.clear_generating(phone)
