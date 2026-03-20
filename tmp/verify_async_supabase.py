import asyncio
import os
from supabase import create_client, ClientOptions
from dotenv import load_dotenv

async def test_async():
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    print(f"Testing Async Supabase to {url}...")
    
    try:
        from app.supabase_client import supabase_client
        client = await supabase_client.get_client()
        
        print("Executing async select...")
        start = asyncio.get_event_loop().time()
        result = await client.table("leads").select("*").limit(1).execute()
        end = asyncio.get_event_loop().time()
        print(f"Success! Found {len(result.data)} leads in {end-start:.2f}s")
    except Exception as e:
        print(f"Async failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_async())
