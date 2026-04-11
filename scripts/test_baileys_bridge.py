import asyncio
import json
import redis.asyncio as redis
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

async def mock_baileys_flow():
    # Use settings from config if available or default
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    r = redis.from_url(REDIS_URL, decode_responses=True)
    
    test_phone = "whatsapp:+447700900000"
    test_message = "Hi Mark, I'm testing the new Baileys integration. Can you hear me?"
    test_id = "test_msg_id_123"

    print(f"[Mock] Publishing test message for {test_phone}")
    
    payload = {
        "from": "447700900000@s.whatsapp.net",
        "message": test_message,
        "timestamp": 123456789,
        "messageId": test_id
    }
    
    # 1. Clear session for clean test
    await r.delete(f"session:{test_phone}")
    await r.delete(f"buffer:{test_phone}")
    
    # 2. Subscribe to outbound to catch the reply
    pubsub = r.pubsub()
    await pubsub.subscribe("outbound")
    
    # 3. Publish inbound
    await r.publish("inbound", json.dumps(payload))
    print(f"OK [Mock] Published to 'inbound'")

    print(f"WAIT [Mock] Waiting for response on 'outbound' channel (max 30s)...")
    
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < 30:
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if msg:
            data = json.loads(msg["data"])
            print(f"\nRECEIVED RESPONSE ON OUTBOUND:")
            print(f"Target: {data['to']}")
            print(f"Reply: {data['response']}")
            print(f"ReplyTo: {data['replyToMessageId']}")
            
            if data['to'] == test_phone:
                print("\nOK Verification SUCCESS: Bridge is working perfectly!")
                return
        await asyncio.sleep(0.1)

    print("\n??? Verification FAILED: Timeout waiting for response.")

if __name__ == "__main__":
    asyncio.run(mock_baileys_flow())
