import asyncio
from app.supabase_client import supabase_client

async def check_messages():
    phone = "whatsapp:+918160178327"
    print(f"Checking messages for {phone}...")
    
    client = await supabase_client.get_client()
    
    # Get lead ID first
    res_lead = await client.table("leads").select("id").eq("phone", phone).execute()
    if not res_lead.data:
        print("❌ Lead not found.")
        return
        
    lead_id = res_lead.data[0]['id']
    print(f"Found Lead ID: {lead_id}")
    
    # Check messages
    res_msg = await client.table("messages").select("*").eq("lead_id", lead_id).order("created_at", desc=True).limit(5).execute()
    
    if res_msg.data:
        for m in res_msg.data:
            print(f"--- Message ({m['created_at']}) ---")
            print(f"Role: {m.get('role', 'N/A')}")
            print(f"Content: {m.get('content', '')[:50]}...")
    else:
        print("❌ No messages found for this lead.")

if __name__ == "__main__":
    asyncio.run(check_messages())
