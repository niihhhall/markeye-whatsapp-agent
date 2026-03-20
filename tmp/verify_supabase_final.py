import asyncio
import os
from app.supabase_client import supabase_client
from app.config import settings

async def verify_supabase_data():
    print(f"Checking Supabase at: {settings.SUPABASE_URL}")
    client = await supabase_client.get_client()
    
    # Check leads
    print("\n--- Recent Leads ---")
    result = await client.table("leads").select("*").order("created_at", desc=True).limit(5).execute()
    if result.data:
        for lead in result.data:
            print(f"ID: {lead.get('id')} | Name: {lead.get('first_name') or lead.get('name')} | Phone: {lead.get('phone')} | Source: {lead.get('lead_source')}")
    else:
        print("No leads found.")

    # Check conversation states
    print("\n--- Recent Conversation States ---")
    result = await client.table("conversation_state").select("*").order("updated_at", desc=True).limit(5).execute()
    if result.data:
        for state in result.data:
            print(f"Lead ID: {state.get('lead_id')} | State: {state.get('current_state')} | Updated: {state.get('updated_at')}")
    else:
        print("No states found.")

if __name__ == "__main__":
    asyncio.run(verify_supabase_data())
