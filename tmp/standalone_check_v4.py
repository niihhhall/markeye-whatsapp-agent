import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def check():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    
    # Try querying scenario and ideal_response
    try:
        r = httpx.get(f"{url}/rest/v1/dynamic_training?select=scenario,ideal_response&limit=1", headers=headers)
        print(f"Status dynamic_training (scenario/ideal_response): {r.status_code}")
        if r.status_code == 200:
            print("Successfully queried scenario/ideal_response")
        else:
            print(f"Error: {r.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    check()
