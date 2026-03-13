import sys
import os

# Add the project root to sys.path so we can import 'app'
sys.path.append(os.getcwd())

from app.tracker import AlbertTracker
from app.supabase_client import supabase_client

def fetch_live_logs(phone: str, limit: int = 5):
    print(f"📡 Connecting to Supabase for {phone}...")
    tracker = AlbertTracker()
    
    try:
        lead = tracker.get_lead_by_phone(phone)
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return
    
    if not lead:
        print(f"❌ Lead with phone {phone} not found in Supabase.")
        return

    lead_id = lead["id"]
    print(f"✅ Found Lead: {lead.get('first_name', 'Unknown')} {lead.get('last_name', '')}")
    print(f"📊 Signal Score: {lead.get('signal_score', 0)}/10 | Temperature: {lead.get('temperature', 'Cold')}")
    print(f"🎯 Outcome: {lead.get('outcome', 'In Progress')}")
    print("-" * 50)

    # Fetch last messages via synchronous execute()
    try:
        print(f"📥 Fetching last {limit} messages...")
        result = supabase_client.client.table("messages")\
            .select("*")\
            .eq("lead_id", lead_id)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()

        messages = result.data
        if not messages:
            print("📭 No messages found for this lead yet.")
        else:
            print(f"📜 Latest Conversation (Last {len(messages)}):")
            # Reverse to show chronological order
            for msg in reversed(messages):
                direction = "👤 Lead" if msg["direction"] == "inbound" else "🤖 Albert"
                time_str = msg.get("created_at", "").split(".")[0].replace("T", " ")
                print(f"[{time_str}] {direction}: {msg['content']}")
        print("-" * 50)
    except Exception as e:
        print(f"❌ Error fetching messages: {e}")

if __name__ == "__main__":
    phone = sys.argv[1] if len(sys.argv) > 1 else "whatsapp:+918160178327"
    fetch_live_logs(phone)
