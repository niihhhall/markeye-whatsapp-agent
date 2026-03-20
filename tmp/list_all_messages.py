import asyncio
from app.supabase_client import supabase_client
from datetime import datetime, timedelta, timezone

async def list_messages():
    out_file = "tmp/all_messages_debug.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("Listing last 20 messages from Supabase...\n")
        
        try:
            client = await supabase_client.get_client()
            res = await client.table("messages").select("*").order("created_at", desc=True).limit(20).execute()
            
            if res.data:
                f.write(f"✅ Found {len(res.data)} messages.\n")
                for m in res.data:
                    f.write(f"--- Message ({m['created_at']}) ---\n")
                    f.write(f"Lead ID: {m.get('lead_id')}\n")
                    f.write(f"Role: {m.get('role', 'N/A')}\n")
                    f.write(f"Content: {m.get('content', '')[:100]}\n")
            else:
                f.write("❌ No messages found.\n")
                
        except Exception as e:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(list_messages())
