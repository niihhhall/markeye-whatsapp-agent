import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def check():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    
    # Try querying EVERYTHING
    try:
        r = httpx.get(f"{url}/rest/v1/dynamic_training?limit=1", headers=headers)
        if r.status_code == 200:
            data = r.json()
            if data:
                print(f"FULL ROW KEYS: {list(data[0].keys())}")
                print(f"FULL ROW DATA: {data[0]}")
            else:
                print("dynamic_training is empty.")
        else:
            print(f"Error: {r.text}")
            
        # Check training_data too
        r2 = httpx.get(f"{url}/rest/v1/training_data?limit=1", headers=headers)
        if r2.status_code == 200 and r2.json():
            print(f"training_data KEYS: {list(r2.json()[0].keys())}")
        
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    check()
