import asyncio
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from app.supabase_client import supabase_client

async def check_training_data_schema():
    print("--- 🔍 Checking 'training_data' Schema ---")
    client = await supabase_client.get_client()
    try:
        # Try to select a non-existent column to get the error message which lists valid columns in some DBs, 
        # or just try to insert a dummy row and catch the error.
        # But even better: Use Postgrest's explanation if possible, or just try common names.
        
        # Let's try to fetch the first row's keys. Since it's empty, we'll try to insert and then rollback or delete.
        dummy_data = {"category": "test_probe"}
        res = await client.table("training_data").insert(dummy_data).execute()
        if res.data:
            print(f"✅ Successfully inserted probe. Columns: {list(res.data[0].keys())}")
            # Clean up
            await client.table("training_data").delete().eq("id", res.data[0]["id"]).execute()
        else:
            print("❌ Probe insert failed without data.")
    except Exception as e:
        print(f"Probe Error: {e}")
        # Error often contains the list of valid columns if we send an invalid one.
        try:
            # Send a guaranteed invalid column
            await client.table("training_data").select("guaranteed_invalid_column_name").execute()
        except Exception as e2:
            print(f"Schema Hint from Error: {e2}")

if __name__ == "__main__":
    asyncio.run(check_training_data_schema())
