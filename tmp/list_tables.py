import asyncio
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from app.supabase_client import supabase_client

async def list_tables():
    print("--- 🔍 Listing Supabase Tables ---")
    client = await supabase_client.get_client()
    try:
        # This is a bit hacky as postgrest-py doesn't have a direct list_tables,
        # but we can try common ones or check the response from an invalid query
        tables = ["training_data", "dynamic_training", "leads", "conversations"]
        for t in tables:
            try:
                res = await client.table(t).select("*").limit(1).execute()
                print(f"✅ Table '{t}' exists.")
                if res.data:
                    print(f"   Columns: {list(res.data[0].keys())}")
                else:
                    print("   (Empty)")
            except Exception as e:
                print(f"❌ Table '{t}' does not exist or error: {e}")
    except Exception as e:
        print(f"Root Error: {e}")

if __name__ == "__main__":
    asyncio.run(list_tables())
