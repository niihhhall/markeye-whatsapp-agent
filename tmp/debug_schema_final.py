import asyncio
from app.config import settings
from supabase import create_client

async def check_schema():
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_KEY
    supabase = create_client(url, key)
    
    # Try to fetch one row from dynamic_training
    try:
        res = supabase.table("dynamic_training").select("*").limit(1).execute()
        if res.data:
            print(f"Columns in dynamic_training: {list(res.data[0].keys())}")
        else:
            print("Table dynamic_training is empty.")
            # Let's try to describe it or just check another table
            res2 = supabase.table("training_data").select("*").limit(1).execute()
            if res2.data:
                print(f"Columns in training_data: {list(res2.data[0].keys())}")
    except Exception as e:
        print(f"Error checking dynamic_training: {e}")
        # Try training_data as fallback
        try:
             res3 = supabase.table("training_data").select("*").limit(1).execute()
             if res3.data:
                 print(f"Columns in training_data fallback: {list(res3.data[0].keys())}")
        except Exception as e2:
             print(f"Error checking training_data: {e2}")

if __name__ == "__main__":
    asyncio.run(check_schema())
