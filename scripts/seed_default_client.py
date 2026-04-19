import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from supabase import create_client, Client

async def seed_default_client():
    print("START: Seeding Default Client into Supabase...")
    
    try:
        supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        
        # Check if default client exists
        existing = supabase.table("clients").select("id").eq("business_name", "Markeye AI").execute()
        
        if existing.data:
            print("INFO: Default client 'Markeye AI' already exists.")
            return

        client_data = {
            "business_name": "Markeye AI",
            "whatsapp_number": "whatsapp:+default", # Placeholder
            "system_prompt": "You are Mark, an AI SDR for Markeye. Your goal is to qualify leads and book calls.",
            "qualification_questions": [
                "What is your current monthly ad spend?",
                "Are you the primary decision maker?",
                "What is your biggest bottleneck right now?"
            ],
            "calendly_link": settings.CALENDLY_LINK,
            "greeting_message": "Hey! Thanks for reaching out. I'm Mark from Markeye. Quick question — what made you fill out the form today?",
            "messaging_provider": "whatsapp_cloud",
            "outreach_template_name": "markeye_outreach",
            "whatsapp_phone_number_id": settings.WHATSAPP_PHONE_NUMBER_ID,
            "whatsapp_access_token": settings.WHATSAPP_ACCESS_TOKEN,
            "active": True
        }
        
        response = supabase.table("clients").insert(client_data).execute()
        print(f"??? Successfully seeded default client: {response.data[0]['id']}")
        
    except Exception as e:
        print(f"??? Seeding failed: {e}")
        if "relation \"clients\" does not exist" in str(e):
            print("???? Error: The 'clients' table was not found. Please ensure you have run the SQL migrations in the Supabase Dashboard.")

if __name__ == "__main__":
    asyncio.run(seed_default_client())
