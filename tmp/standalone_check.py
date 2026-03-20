import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def check():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        return

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}"
    }
    
    # Check dynamic_training
    try:
        r = httpx.get(f"{url}/rest/v1/dynamic_training?limit=1", headers=headers)
        print(f"Status dynamic_training: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if data:
                print(f"Columns in dynamic_training: {list(data[0].keys())}")
            else:
                print("dynamic_training is empty.")
        else:
            print(f"Error: {r.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    check()
