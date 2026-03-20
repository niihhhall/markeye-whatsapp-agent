import asyncio
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from app.supabase_client import supabase_client

async def check_columns():
    print("--- 🔍 Checking 'training_data' Columns ---")
    client = await supabase_client.get_client()
    try:
        # Fetch one record to see keys
        result = await client.table("training_data").select("*").limit(1).execute()
        if result.data:
            print(f"Columns found: {list(result.data[0].keys())}")
        else:
            print("Table is empty, checking schema via another method...")
            # Try to fetch column names from postgrest if possible, but simplest is to check the table definition
            # Or just check what's there
            print("No data in table to inspect.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_columns())
