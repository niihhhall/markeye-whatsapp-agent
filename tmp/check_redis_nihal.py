import asyncio
from app.redis_client import redis_client

async def check_redis():
    phone = "whatsapp:+918160178327"
    print(f"Checking Redis session for {phone}...")
    
    try:
        session = await redis_client.get_session(phone)
        if session:
            print(f"✅ Session found!")
            print(f"   State: {session.get('state')}")
            print(f"   Turn Count: {session.get('turn_count')}")
            history = session.get('history', [])
            print(f"   History length: {len(history)}")
            if history:
                print(f"   Latest role: {history[-1].get('role')}")
        else:
            print("❌ No session found in Redis.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_redis())
