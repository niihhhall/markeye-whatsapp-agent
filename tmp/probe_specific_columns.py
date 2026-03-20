import asyncio
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from app.supabase_client import supabase_client

async def probe_trigger_response():
    print("--- 🔍 Probing 'training_data' for 'trigger'/'response' ---")
    client = await supabase_client.get_client()
    try:
        # Try to select trigger, response
        res = await client.table("training_data").select("trigger,response").limit(1).execute()
        print("✅ Columns 'trigger' and 'response' exist in 'training_data'.")
    except Exception as e:
        print(f"❌ Columns 'trigger' or 'response' do NOT exist in 'training_data': {e}")
        
    try:
        # Check subcategory and ideal_response too
        res = await client.table("training_data").select("subcategory,ideal_response").limit(1).execute()
        print("✅ Columns 'subcategory' and 'ideal_response' exist.")
    except Exception as e:
        print(f"❌ Columns 'subcategory' or 'ideal_response' do NOT exist: {e}")

if __name__ == "__main__":
    asyncio.run(probe_trigger_response())
