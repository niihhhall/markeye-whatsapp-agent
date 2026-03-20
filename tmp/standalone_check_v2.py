import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def check():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    
    # Check training_data
    try:
        r = httpx.get(f"{url}/rest/v1/training_data?limit=1", headers=headers)
        if r.status_code == 200 and r.json():
            print(f"Columns in training_data: {list(r.json()[0].keys())}")
        else:
            print(f"training_data empty or error: {r.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    check()
