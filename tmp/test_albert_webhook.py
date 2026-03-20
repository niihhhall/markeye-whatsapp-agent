import requests
import time

def test_albert_webhook():
    url = "http://localhost:8000/form-webhook"  # Local test
    # url = "https://after5-agent-production.up.railway.app/form-webhook" # Production test
    
    payload = {
        "first_name": "Test",
        "name": "Test User",
        "phone": "+918800557262", # Replace with actual test phone if needed
        "company": "Test Company",
        "industry": "Tech",
        "message": "Testing Albert connection from Demo",
        "source": "verification_script"
    }

    print(f"Sending test payload to {url}...")
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.json()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_albert_webhook()
