import asyncio
from app.supabase_client import supabase_client

async def list_last_leads():
    out_file = "tmp/last_leads_debug.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("Listing last 10 leads from Supabase...\n")
        
        try:
            client = await supabase_client.get_client()
            res = await client.table("leads").select("*").order("created_at", desc=True).limit(10).execute()
            
            if res.data:
                f.write(f"✅ Found {len(res.data)} leads.\n")
                for l in res.data:
                    f.write(f"--- Lead ({l['created_at']}) ---\n")
                    f.write(f"Name: {l.get('first_name')} {l.get('last_name', '')}\n")
                    f.write(f"Phone: {l.get('phone')}\n")
                    f.write(f"Source: {l.get('lead_source', 'N/A')}\n")
            else:
                f.write("❌ No leads found at all.\n")
                
        except Exception as e:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(list_last_leads())
