import httpx
import asyncio
import json

# Replace with your local URL (e.g., http://localhost:8080) or Railway URL
BASE_URL = "https://after5-agent-production.up.railway.app" 

async def test_text_message(text: str):
    print(f"\n--- Testing Text Message: '{text}' ---")
    payload = {
        "service": "channels",
        "event": "whatsapp.inbound",
        "payload": {
            "id": "test-msg-id-123",
            "channelId": "test-channel-id",
            "sender": {
                "contact": {
                    "identifierValue": "+918160178327",
                    "annotations": {"name": "Test User"}
                }
            },
            "body": {
                "type": "text",
                "text": {"text": text}
            }
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{BASE_URL}/webhook", json=payload)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")

async def test_voice_note(audio_url: str):
    print(f"\n--- Testing Voice Note: {audio_url} ---")
    payload = {
        "service": "channels",
        "event": "whatsapp.inbound",
        "payload": {
            "id": "test-msg-id-456",
            "channelId": "test-channel-id",
            "sender": {
                "contact": {
                    "identifierValue": "+918160178327",
                    "annotations": {"name": "Test User"}
                }
            },
            "body": {
                "type": "audio",
                "audio": {"url": audio_url}
            }
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{BASE_URL}/webhook", json=payload)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")

async def main():
    print("Albert Test Suite starting...")
    
    # 1. Test a simple text message
    await test_text_message("How can After5 help me with my business?")
    
    # 2. Test a voice note (using a sample publicly accessible audio if possible)
    # await test_voice_note("https://www.learningcontainer.com/wp-content/uploads/2020/02/Sample-OGG-File.ogg")

    print("\nTest finished. Now check your terminal/Railway logs for Albert's thinking process!")

if __name__ == "__main__":
    asyncio.run(main())
