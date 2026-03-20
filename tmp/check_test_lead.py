import asyncio
from app.supabase_client import supabase_client
from app.redis_client import redis_client

async def check_lead_status():
    phone = "whatsapp:+918160178327"
    print(f"Checking status for {phone}...")
    
    # 1. Check Supabase
    client = await supabase_client.get_client()
    res = await client.table("leads").select("*").eq("phone", phone).order("created_at", desc=True).limit(1).execute()
    
    if res.data:
        lead = res.data[0]
        print(f"✅ Latest Lead: {lead['first_name']} {lead.get('last_name', '')}")
        print(f"   Created at (UTC): {lead['created_at']}")
        print(f"   Internal ID: {lead['id']}")
        print(f"   Source: {lead.get('lead_source', 'N/A')}")
    else:
        print("❌ Lead NOT found in Supabase.")

    # 2. Check Redis
    session = await redis_client.get_session(phone)
    if session:
        print("✅ Session found in Redis.")
        print(f"   State: {session.get('state')}")
        print(f"   History length: {len(session.get('history', []))}")
    else:
        print("❌ Session NOT found in Redis.")

if __name__ == "__main__":
    asyncio.run(check_lead_status())
