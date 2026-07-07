import pytest
import hmac
import hashlib
import json
import time
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from app.webhook import verify_openwa_signature
from app.message_router import send_message

client = TestClient(app)


def test_verify_openwa_signature():
    secret = "test_secret"
    body = b"hello world"
    expected_hash = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    
    # 1. Test standard signature
    assert verify_openwa_signature(body, expected_hash, secret) is True
    
    # 2. Test signature with sha256= prefix
    assert verify_openwa_signature(body, f"sha256={expected_hash}", secret) is True
    
    # 3. Test invalid signature
    assert verify_openwa_signature(body, "invalid", secret) is False
    assert verify_openwa_signature(body, "", secret) is False


@patch("app.webhook.settings")
@patch("app.webhook.redis_client")
def test_openwa_webhook_ignored_event(mock_redis, mock_settings):
    # Disable signature verification for this test
    mock_settings.OPENWA_WEBHOOK_SECRET = ""
    
    payload = {"event": "chat_state", "data": {}}
    response = client.post("/webhook/openwa", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@patch("app.webhook.settings")
@patch("app.webhook.redis_client")
def test_openwa_webhook_empty_data(mock_redis, mock_settings):
    # Disable signature verification
    mock_settings.OPENWA_WEBHOOK_SECRET = ""
    
    payload = {"event": "message", "data": {}}
    response = client.post("/webhook/openwa", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@patch("app.webhook.settings")
@patch("app.webhook.redis_client")
@patch("app.webhook.delayed_buffer_process")
@patch("app.webhook.hard_max_check")
@patch("app.webhook.background_tracker_log")
def test_openwa_webhook_valid_message(mock_tracker, mock_hard, mock_delayed, mock_redis, mock_settings):
    # Disable signature verification
    mock_settings.OPENWA_WEBHOOK_SECRET = ""
    
    mock_redis.redis = AsyncMock()
    mock_redis.redis.incr = AsyncMock(return_value=1)
    mock_redis.redis.expire = AsyncMock()
    mock_redis.redis.get = AsyncMock(return_value=None)
    mock_redis.redis.set = AsyncMock()
    
    mock_redis.buffer_message = AsyncMock(return_value=("batch_123", True))
    mock_redis.get_session = AsyncMock(return_value=None)
    mock_redis.check_and_clear_stale_generation = AsyncMock()
    
    payload = {
        "event": "message",
        "sessionId": "client_abc",
        "data": {
            "id": "msg_12345",
            "from": "447700900000@c.us",
            "to": "447700900001@c.us",
            "body": "Hello SDR",
            "timestamp": int(time.time()),
            "self": False,
            "sender": {"pushname": "Nihal"}
        }
    }
    
    response = client.post("/webhook/openwa", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    
    mock_redis.buffer_message.assert_called_once_with("whatsapp:+447700900000", "Hello SDR")


@pytest.mark.asyncio
@patch("app.message_router._call_openwa_api")
async def test_send_message_openwa(mock_api):
    mock_api.return_value = {"id": "msg_sent_999"}
    
    client_config = {
        "id": "client_abc",
        "messaging_provider": "openwa"
    }
    
    res = await send_message("whatsapp:+447700900000", "Testing OpenWA send", client_config=client_config)
    assert res is not None
    assert res["status"] == "sent"
    assert res["provider"] == "openwa"
    assert res["messageId"] == "msg_sent_999"
    
    mock_api.assert_called_once_with(
        "/messages/send-text",
        {
            "sessionId": "client_abc",
            "to": "447700900000@c.us",
            "text": "Testing OpenWA send"
        }
    )
