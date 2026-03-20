import asyncio
from app.supabase_client import supabase_client
from datetime import datetime, timedelta, timezone

async def check_recent_leads():
    out_file = "tmp/recent_leads_check.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("Checking for leads created in the last 30 minutes...\n")
        
        try:
            client = await supabase_client.get_client()
            
            # Simple select all and filter in python if needed, or use query
            # Current time (UTC)
            now = datetime.now(timezone.utc)
            thirty_mins_ago = (now - timedelta(minutes=30)).isoformat()
            
            res = await client.table("leads").select("*").gt("created_at", thirty_mins_ago).order("created_at", desc=True).execute()
            
            if res.data:
                f.write(f"✅ Found {len(res.data)} recent leads.\n")
                for l in res.data:
                    f.write(f"--- Lead ({l['created_at']}) ---\n")
                    f.write(f"Name: {l.get('first_name')} {l.get('last_name', '')}\n")
                    f.write(f"Phone: {l.get('phone')}\n")
                    f.write(f"Source: {l.get('lead_source', 'N/A')}\n")
            else:
                f.write("❌ No leads found in the last 30 minutes.\n")
                
        except Exception as e:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(check_recent_leads())
