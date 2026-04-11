import asyncio
import sys
import os
import time

# Ensure we can import from the root app directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.config import settings
from app.redis_client import redis_client
from supabase import create_client, Client
import httpx

async def check_redis():
    print("--- [Redis Audit] ---")
    start = time.time()
    is_up = await redis_client.ping()
    latency = (time.time() - start) * 1000
    if is_up:
        print(f"OK: Redis Connected Successfully (Latency: {latency:.2f}ms)")
    else:
        print("ERROR: Redis Connection Failed")
    return is_up

async def check_supabase():
    print("\n--- [Supabase Audit] ---")
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        # Check for existence of core tables
        try:
            supabase.table("leads").select("id").limit(1).execute()
            print("OK: Supabase Table 'leads' detected")
            
            # Check for clients table
            supabase.table("clients").select("qualification_questions").limit(1).execute()
            print("OK: Supabase Table 'clients' and column 'qualification_questions' detected")
        except Exception as e:
            print(f"WARN: Supabase Table 'clients' or column missing: {e}")
            return False
            
        print(f"OK: Supabase Base Connection OK")
        return True
    except Exception as e:
        print(f"??? Supabase Connection Failed: {e}")
        return False

async def check_llms():
    print("\n--- [LLM Connectivity Audit] ---")
    providers = {
        "Groq": settings.GROQ_API_KEY,
        "Gemini": settings.GEMINI_API_KEY,
        "Cerebras": settings.CEREBRAS_API_KEY
    }
    
    for name, key in providers.items():
        if key:
            if len(key) > 10:
                print(f"OK: {name} API Key present")
            else:
                print(f"ERROR: {name} API Key looks invalid")
        else:
            print(f"ERROR: {name} API Key MISSING")

async def main():
    print("[Start] Markeye Infrastructure Diagnostic Audit...")
    print(f"Environment: {settings.ENVIRONMENT}\n")
    
    redis_ok = await check_redis()
    supabase_ok = await check_supabase()
    await check_llms()
    
    print("\n--- Audit Summary ---")
    if redis_ok and supabase_ok:
        print("??? Infrastructure is STABLE and ready for launch.")
        print("???? Action: Run 'npm start' in baileys-service and scan QR code.")
    else:
        print("??? Critical issues detected. Please check your .env configuration.")
        if not supabase_ok:
            print("???? Reminder: Ensure you have run the consolidated SQL in the Supabase Dashboard.")

if __name__ == "__main__":
    asyncio.run(main())
