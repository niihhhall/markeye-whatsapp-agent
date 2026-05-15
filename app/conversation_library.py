import json
import os
import logging
import random
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = os.path.join(os.getcwd(), "conversations")


async def load_conversation_library(redis):
    """Load all example conversations into Redis on app startup using a pipeline."""
    if not os.path.exists(CONVERSATIONS_DIR):
        logger.warning(f"Conversations directory not found: {CONVERSATIONS_DIR}")
        return
    
    count = 0
    # Use pipeline for atomicity and speed (reduces 52 round-trips to 1)
    async with redis.pipeline(transaction=False) as pipe:
        for filename in os.listdir(CONVERSATIONS_DIR):
            if not filename.endswith(".json"):
                continue
            
            filepath = os.path.join(CONVERSATIONS_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                conv_id = data["id"]
                tags = data["tags"]
                ttl = 86400 * 30
                
                # Store the full conversation
                pipe.set(f"conv_example:{conv_id}", json.dumps(data), ex=ttl)
                
                # Index by industry
                industry = tags.get("industry", "general")
                pipe.sadd(f"conv_index:industry:{industry}", conv_id)
                pipe.expire(f"conv_index:industry:{industry}", ttl)
                
                # Index by stage
                stage = tags.get("stage", "general")
                pipe.sadd(f"conv_index:stage:{stage}", conv_id)
                pipe.expire(f"conv_index:stage:{stage}", ttl)
                
                # Index by objection type
                for obj in tags.get("objections", []):
                    if obj != "none":
                        pipe.sadd(f"conv_index:objection:{obj}", conv_id)
                        pipe.expire(f"conv_index:objection:{obj}", ttl)
                
                # Index by personality
                personality = tags.get("personality", "unknown")
                pipe.sadd(f"conv_index:personality:{personality}", conv_id)
                pipe.expire(f"conv_index:personality:{personality}", ttl)
                
                count += 1
            except Exception as e:
                logger.error(f"Error preparing {filename} for pipeline: {e}")
        
        # Execute all commands at once
        await pipe.execute()
    
    logger.info(f"Loaded {count} example conversations into library via pipeline")


async def get_relevant_example(
    redis,
    industry: str = "",
    stage: str = "",
    objection: str = "",
    personality: str = ""
) -> str:
    """
    Find the most relevant example conversation based on current context.
    Priority: objection > industry + stage > personality > random
    Returns formatted conversation text for injection into LLM context.
    """
    conv_id = None
    
    # Priority 1: Match by objection (most specific)
    if objection:
        members = await redis.smembers(f"conv_index:objection:{objection}")
        if members:
            conv_id = list(members)[0]
            if isinstance(conv_id, bytes):
                conv_id = conv_id.decode()
    
    # Priority 2: Match by industry
    if not conv_id and industry:
        members = await redis.smembers(f"conv_index:industry:{industry}")
        if members:
            member_list = [m.decode() if isinstance(m, bytes) else m for m in members]
            conv_id = random.choice(member_list)
    
    # Priority 3: Match by personality
    if not conv_id and personality:
        members = await redis.smembers(f"conv_index:personality:{personality}")
        if members:
            member_list = [m.decode() if isinstance(m, bytes) else m for m in members]
            conv_id = random.choice(member_list)
    
    # Priority 4: Match by stage
    if not conv_id and stage:
        members = await redis.smembers(f"conv_index:stage:{stage}")
        if members:
            member_list = [m.decode() if isinstance(m, bytes) else m for m in members]
            conv_id = random.choice(member_list)
            
    # Priority 5: Fallback to a random general example if nothing matched
    if not conv_id:
        members = await redis.smembers("conv_index:industry:general")
        if members:
            member_list = [m.decode() if isinstance(m, bytes) else m for m in members]
            conv_id = random.choice(member_list)
    
    if not conv_id:
        return ""
    
    # Fetch and format
    data = await redis.get(f"conv_example:{conv_id}")
    if not data:
        return ""
    
    conv = json.loads(data)
    return _format_conversation(conv)


def _format_conversation(conv: dict) -> str:
    """Format a conversation example for LLM context injection."""
    lines = []
    lines.append(f"Reference conversation ({conv['id']}):")
    for msg in conv["conversation"]:
        role = "Albert" if msg["role"] == "albert" else "Lead"
        lines.append(f"{role}: {msg['text']}")
    return "\n".join(lines)
