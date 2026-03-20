import asyncio
import httpx

async def test_webhook():
    url = "https://after5-agent-production.up.railway.app/form-webhook"
    payload = {
        "first_name": "Antigravity",
        "name": "Antigravity Test",
        "phone": "+918160178327", # Testing with a known number
        "company": "Antigravity AI",
        "industry": "AI",
        "message": "Testing the automated outreach logic.",
        "source": "website_demo_form"
    }
    
    print(f"Sending request to {url}...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_webhook())
