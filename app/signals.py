"""
Buyer signal detection for adjusting Mark's approach.
Analyses lead messages to determine interest level and personality type.
"""

import re


def detect_interest_level(message: str, history: list = None) -> str:
    """
    Classify buyer interest from their message.
    Returns: 'high', 'medium', 'low', or 'buying'
    """
    text = message.lower().strip()
    words = text.split()
    
    # BUYING signals — they're asking about logistics
    buying_patterns = [
        "how much", "what's the cost", "pricing", "how long",
        "when can", "how do we start", "what's the process",
        "next step", "book", "call", "demo", "timeline",
        "how does it work", "can you show", "send the link"
    ]
    if any(p in text for p in buying_patterns):
        return "buying"
    
    # HIGH interest — detailed, engaged, asking questions
    if len(words) > 20 or text.count("?") >= 1:
        if any(w in text for w in ["we", "our", "my team", "currently", "right now"]):
            return "high"
    
    # LOW interest — short, flat, no questions
    if len(words) < 5 and "?" not in text:
        low_patterns = ["ok", "sure", "maybe", "interesting", "cool", "right", "hmm"]
        if any(text.strip().rstrip(".!") == p for p in low_patterns):
            return "low"
    
    return "medium"


def detect_personality_type(messages: list) -> str:
    """
    Classify personality type from message history.
    Returns: 'driver', 'analytical', 'expressive', 'amiable'
    """
    if not messages:
        return "unknown"
    
    avg_length = sum(len(m.split()) for m in messages) / len(messages)
    has_questions = any("?" in m for m in messages)
    has_detail = any(len(m.split()) > 12 for m in messages)
    has_warmth = any(w in " ".join(messages).lower() for w in 
                     ["haha", "lol", "honestly", "to be fair", "you know"])
    
    if avg_length < 8 and not has_detail:
        return "driver"       # Short, direct — wants efficiency
    elif has_detail and has_questions:
        return "analytical"   # Detailed, questioning — wants thoroughness
    elif has_warmth:
        return "expressive"   # Warm, chatty — wants connection
    else:
        return "amiable"      # Vague, agreeable — wants safety


def detect_objection_type(message: str) -> str:
    """Detect the type of objection present in the message."""
    text = message.lower()
    objection_map = {
        "price": ["price", "cost", "damage", "how much"],
        "price_pressure": ["avoiding", "stop avoiding"],
        "bad_experience": ["bad", "awful", "tried before", "chatbots"],
        "tools_comparison": ["chatgpt", "zapier", "manychat", "va"],
        "no_budget": ["budget", "no money"],
        "ai_failure_fear": ["work", "wrong", "error", "fail"],
        "sales_team": ["sales team", "already have"],
        "business_hours": ["business hours", "weekends", "after hours"],
        "proof": ["case study", "proof", "referrals", "clients"],
        "setup_time": ["setup", "how long"],
        "crm": ["crm", "hubspot", "salesforce", "ghl"],
        "ai_readiness": ["ready", "ai isn't"],
        "delayed_action": ["few months", "later"],
        "small_business": ["small business", "too small"],
        "skepticism": ["too good", "scam"],
        "not_interested": ["not interested", "no thanks"],
        "manual_process": ["manually", "manual"],
        "send_info": ["more information", "send info", "website"]
    }
    
    for obj_type, patterns in objection_map.items():
        if any(p in text for p in patterns):
            return obj_type
    return ""


def get_approach_instructions(interest: str, personality: str) -> str:
    """
    Return approach instructions to inject into LLM context.
    Ensures that British Tone and ||| Chunking are still enforced.
    """
    instructions = ["\n═══ DYNAMIC APPROACH INSTRUCTIONS ═══"]
    
    # Interest-based
    if interest == "buying":
        instructions.append(
            "SIGNAL: Lead is showing BUYING signals. They're asking about logistics/process/cost. "
            "Move toward the call efficiently. Don't over-qualify. They're ready."
        )
    elif interest == "high":
        instructions.append(
            "SIGNAL: Lead is highly engaged. Good detail in their messages. "
            "Match their energy. Keep the momentum. Move through SPIN naturally."
        )
    elif interest == "low":
        instructions.append(
            "SIGNAL: Lead is giving short, flat responses. They might be distracted or not hooked yet. "
            "Try a pattern interrupt — a different angle or an implication question that makes them think. "
            "Do NOT push for a call. Re-engage first."
        )
    
    # Personality-based
    if personality == "driver":
        instructions.append(
            "PERSONALITY: Results-oriented. Keep messages EXTREMELY SHORT. Be direct. No fluff. "
            "Get to the point. Respect their time above all."
        )
    elif personality == "analytical":
        instructions.append(
            "PERSONALITY: Analytical. They want depth and specifics. "
            "Be thorough. Show you understand the detail. Logical structure."
        )
    elif personality == "expressive":
        instructions.append(
            "PERSONALITY: Relationship-oriented. Be warm. Build the connection. "
            "Mirror their energy. Don't rush to business. Rapport first."
        )
    elif personality == "amiable":
        instructions.append(
            "PERSONALITY: Consensus-seeker. Low pressure. Emphasise no-obligation. "
            "Make every step feel safe and reversible."
        )
    
    instructions.append("REMINDER: Maintain British Understatement and use ||| naturally for message bubbles.")
    
    return "\n".join(instructions)
