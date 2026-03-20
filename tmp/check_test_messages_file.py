import asyncio
from app.supabase_client import supabase_client
import os

async def check_messages():
    phone = "whatsapp:+918160178327"
    out_file = "tmp/msg_final_check.txt"
    
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"Checking messages for {phone}...\n")
        
        try:
            client = await supabase_client.get_client()
            
            # Get lead ID first
            res_lead = await client.table("leads").select("id").eq("phone", phone).execute()
            if not res_lead.data:
                f.write("❌ Lead not found.\n")
                return
                
            lead_id = res_lead.data[0]['id']
            f.write(f"Found Lead ID: {lead_id}\n")
            
            # Check messages
            res_msg = await client.table("messages").select("*").eq("lead_id", lead_id).order("created_at", desc=True).limit(5).execute()
            
            if res_msg.data:
                f.write(f"✅ Found {len(res_msg.data)} messages.\n")
                for m in res_msg.data:
                    f.write(f"--- Message ({m['created_at']}) ---\n")
                    f.write(f"Role: {m.get('role', 'N/A')}\n")
                    f.write(f"Content: {m.get('content', '')}\n")
            else:
                f.write("❌ No messages found for this lead.\n")
        except Exception as e:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(check_messages())
