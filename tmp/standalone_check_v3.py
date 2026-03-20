import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def check():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    
    # Try a broad select to see if it even exists
    try:
        r = httpx.get(f"{url}/rest/v1/dynamic_training?select=*", headers=headers)
        if r.status_code == 200:
            print("Successfully queried dynamic_training")
            # If empty, we can't see keys this way. 
            # We can try to insert a dummy row or use another method.
        else:
            print(f"Error querying dynamic_training: {r.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    check()
