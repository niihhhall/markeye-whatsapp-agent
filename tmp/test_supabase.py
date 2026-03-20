
import os
import sys
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SERVICE_KEY")

if not url or not key:
    print("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
    sys.exit(1)

print(f"Connecting to {url}...")
try:
    supabase: Client = create_client(url, key)
    # Try a simple select
    print("Executing select query on 'leads' table...")
    response = supabase.table("leads").select("*").limit(1).execute()
    print(f"Success! Found {len(response.data)} leads.")
    if response.data:
        print(f"Sample lead id: {response.data[0]['id']}")
except Exception as e:
    print(f"Connection failed: {e}")
