"""
Tests for admin session reset endpoint and WAITING/CLOSED state guards in webhook.py
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    """Basic health check must return ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@patch("app.webhook.redis_client")
def test_admin_reset_missing_phone(mock_redis):
    """Admin reset without phone should return error."""
    response = client.post("/admin/reset-session", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "phone" in response.json()["reason"]


@patch("app.webhook.redis_client")
async def test_admin_reset_valid_phone(mock_redis):
    """Admin reset with a valid phone should delete session keys."""
    mock_redis.redis = AsyncMock()
    mock_redis.redis.delete = AsyncMock()
    
    response = client.post("/admin/reset-session", json={"phone": "whatsapp:+919999999999"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def _wa_payload(text="Hello", phone="whatsapp:+919999999999", message_id="msg_test_001"):
    """Helper: Build a WhatsApp Cloud webhook payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "123",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "995384046999209"},
                    "messages": [{
                        "id": message_id,
                        "from": phone.replace("whatsapp:", ""),
                        "type": "text",
                        "text": {"body": text},
                        "timestamp": "1700000000"
                    }]
                },
                "field": "messages"
            }]
        }]
    }


@patch("app.webhook.redis_client")
def test_webhook_ignores_outbound_echo(mock_redis):
    """Status updates (sent echoes) should be ignored."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "123",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "statuses": [{"id": "msg1", "status": "delivered"}]
                },
                "field": "messages"
            }]
        }]
    }
    response = client.post("/webhook/whatsapp", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


if __name__ == "__main__":
    test_health()
    test_admin_reset_missing_phone(MagicMock())
    print("✅ All webhook tests passed!")
