import sys
import os
import time

# Add the project root to sys.path so we can import 'app'
sys.path.append(os.getcwd())

from app.tracker import MarkTracker
from app.supabase_client import supabase_client

def fetch_live_logs(phones=None, limit=20, watch=False, monitor_all=False):
    tracker = MarkTracker()
    lead_map = {} # lead_id -> Name
    lead_list = []

    if monitor_all:
        print("🌍 GLOBAL MONITOR MODE: Watching ALL active leads...")
    else:
        print(f"📡 Connecting to Supabase for {len(phones)} lead(s)...")
        for p in phones:
            try:
                lead = tracker.get_lead_by_phone(p)
                if lead:
                    lead_id = lead["id"]
                    name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or p
                    lead_map[lead_id] = name
                    lead_list.append(lead_id)
                    print(f"✅ Found: {name}")
                else:
                    print(f"⚠️ Lead {p} not found.")
            except Exception as e:
                print(f"❌ Error finding {p}: {e}")

    if not monitor_all and not lead_list:
        print("❌ No valid leads to monitor.")
        return

    print("-" * 60)
    last_seen_ts = None

    def print_messages(msg_limit, reset=False):
        nonlocal last_seen_ts
        try:
            query = supabase_client.client.table("messages")\
                .select("*, leads(first_name, last_name, phone)")\
                .order("created_at", desc=True)\
                .limit(msg_limit)
            
            if not monitor_all:
                query = query.in_("lead_id", lead_list)
            
            result = query.execute()
            messages = result.data
            
            if not messages:
                if reset: print("📭 No messages found yet.")
                return

            # Reverse to show chronological order
            new_msgs = []
            for msg in reversed(messages):
                msg_ts = msg.get("created_at")
                if last_seen_ts is None or (msg_ts and msg_ts > last_seen_ts):
                    new_msgs.append(msg)
                    if msg_ts: last_seen_ts = msg_ts
            
            for msg in new_msgs:
                lead_info = msg.get("leads", {})
                lead_name = f"{lead_info.get('first_name', '')} {lead_info.get('last_name', '')}".strip() or lead_info.get("phone", "Unknown")
                
                direction = f"👤 {lead_name}" if msg["direction"] == "inbound" else "🤖 Mark"
                time_str = msg.get("created_at", "").split(".")[0].replace("T", " ")
                print(f"[{time_str}] {direction}: {msg['content']}")
                
        except Exception as e:
            # Silent fail for polling errors to keep UI clean
            pass

    # Initial fetch
    if reset := True:
        mode_str = "Global" if monitor_all else f"{len(lead_list)} lead(s)"
        print(f"📜 Latest {mode_str} History (Last {limit}):")
        print_messages(limit, reset=True)

    if watch:
        print(f"\n👀 WATCH MODE ACTIVE ({'Global' if monitor_all else 'Specific Leads'})")
        print("Waiting for new messages... (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(2)
                print_messages(10)
        except KeyboardInterrupt:
            print("\n🛑 Stopped monitoring.")

if __name__ == "__main__":
    phones = []
    watch_mode = "--watch" in sys.argv
    all_mode = "--all" in sys.argv
    limit_val = 20

    # Parse args manually for simplicity
    for i, arg in enumerate(sys.argv):
        if i == 0: continue
        if arg.startswith("--"):
            if arg == "--limit" and i+1 < len(sys.argv):
                limit_val = int(sys.argv[i+1])
            continue
        # Assume anything else is a phone number
        phones.append(arg)

    if not phones and not all_mode:
        print("Usage: python scripts/monitor_live.py [phone1] [phone2] ... [--watch] [--limit N] [--all]")
        sys.exit(1)

    fetch_live_logs(phones=phones, limit=limit_val, watch=watch_mode, monitor_all=all_mode)
