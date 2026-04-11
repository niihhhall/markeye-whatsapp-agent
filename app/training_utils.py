import json
from typing import List, Dict, Any, Optional

def messages_to_training_format(messages_list: List[Dict[str, Any]], system_prompt: str) -> Optional[Dict[str, Any]]:
    """Convert raw messages from DB to OpenAI training format"""
    formatted = [{"role": "system", "content": system_prompt}]
    
    for msg in messages_list:
        role = "assistant" if msg["direction"] == "outbound" or msg.get("role") == "assistant" else "user"
        formatted.append({"role": role, "content": msg["content"]})
    
    # Validate: must have at least 1 user and 1 assistant message
    has_user = any(m["role"] == "user" for m in formatted)
    has_assistant = any(m["role"] == "assistant" for m in formatted)
    
    if not (has_user and has_assistant):
        return None  # Skip incomplete conversations
    
    return {"messages": formatted}

def validate_jsonl_line(line: str) -> bool:
    """Validate a single JSONL training line"""
    try:
        data = json.loads(line)
        if "messages" not in data:
            return False
        # system + 1 user + 1 assistant
        if len(data["messages"]) < 3:
            return False
        if data["messages"][0]["role"] != "system":
            return False
        return True
    except:
        return False

def estimate_tokens(text: str) -> int:
    """Rough token count estimate (4 chars per token average)"""
    return len(text) // 4

def get_training_stats(jsonl_content: str) -> Dict[str, Any]:
    """Analyze a JSONL training file content"""
    lines = [l for l in jsonl_content.strip().split('\n') if l.strip()]
    total_convos = len(lines)
    total_tokens = sum(estimate_tokens(line) for line in lines)
    
    total_turns = 0
    for line in lines:
        try:
            data = json.loads(line)
            total_turns += len(data["messages"])
        except:
            continue
            
    avg_turns = total_turns / total_convos if total_convos > 0 else 0
    
    return {
        "conversations": total_convos,
        "estimated_tokens": total_tokens,
        "average_turns": round(avg_turns, 1),
        "estimated_cost_fireworks": f"${total_tokens * 0.000001:.2f}",
        "estimated_cost_modal": f"${total_tokens * 0.0000005:.2f}"
    }
