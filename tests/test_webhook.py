import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_webhook_valid():
    payload = {
        "From": "whatsapp:+1234567890",
        "Body": "Hello!",
        "MessageSid": "SM123"
    }
    response = client.post("/webhook", data=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_form_webhook():
    payload = {
        "name": "John Doe",
        "phone": "+1234567890",
        "company": "ACME Inc"
    }
    response = client.post("/form-webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "outreach_scheduled"
