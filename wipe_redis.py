import redis
import os

REDIS_URL = "redis://markeye-whatsapp-agent-redis.1lpasj.ng.0001.aps1.cache.amazonaws.com:6379"
SESSION_ID = "eb89a504-7a6d-453f-89cd-3c95ed2a22f1"

def wipe_session():
    r = redis.from_url(REDIS_URL)
    pattern = f"baileys:auth:{SESSION_ID}:*"
    keys = r.keys(pattern)
    
    if keys:
        print(f"Found {len(keys)} stale session keys. Wiping...")
        r.delete(*keys)
        print("Wipe complete.")
    else:
        print("No stale session keys found.")
        
    # Also clear active session flags
    r.srem("baileys:active_sessions", SESSION_ID)
    r.delete(f"baileys:session_active:{SESSION_ID}")
    print("Session flags cleared.")

if __name__ == "__main__":
    wipe_session()
