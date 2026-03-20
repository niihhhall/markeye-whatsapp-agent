import requests
import json

BASE_URL = "http://localhost:8000/training"

def test_add_example():
    payload = {
        "title": "Test from Script",
        "tags": {"industry": "test"},
        "history": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]
    }
    
    try:
        response = requests.post(f"{BASE_URL}/library", json=payload)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_add_example()
