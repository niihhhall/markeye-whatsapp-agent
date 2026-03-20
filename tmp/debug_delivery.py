import asyncio
import os
import sys

# Add working directory to sys.path
sys.path.append(os.getcwd())

from app.supabase_client import supabase_client

async def check():
    try:
        client = await supabase_client.get_client()
        lead_id = '981d3891-9ae7-4be6-bede-cd49d20221f8'
        
        print(f"Fetching logs for Lead ID: {lead_id}...")
        
        res = await client.table('messages').select('*').eq('lead_id', lead_id).execute()
        
        if res.data:
            print(f"Found {len(res.data)} total messages for this lead.")
            for m in res.data:
                print(f"[{m.get('created_at')}] [{m.get('direction', '??').upper()}] {m.get('content', '')[:50]}")
        else:
            print(f'No messages found for lead {lead_id}')
            
    except Exception as e:
        print(f"Error during check: {e}")

if __name__ == '__main__':
    asyncio.run(check())
