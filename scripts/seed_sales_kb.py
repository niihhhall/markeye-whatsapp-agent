import asyncio
import os
import sys

# Add project root to sys.path so we can import app modules
sys.path.append(os.getcwd())

from app.redis_client import redis_client

SALES_INTEL = {
    "rag:sales:psychology": """
═══ SALES PSYCHOLOGY REFERENCE ═══
- EMOTION FIRST: Surface the pain before presenting logic.
- LOSS AVERSION: Frame around ad spend already paid for that's going cold.
- CERTAINTY GAP: Make the next step (free call) feel zero-risk.
- IKEA EFFECT: Lead must articulate the problem themselves to value the solution.
- COGNITIVE LOAD: One thought per message. One question at a time.
""",
    "rag:sales:spin": """
═══ SPIN SELLING FRAMEWORK ═══
- SITUATION (S): Basics only. "How do you handle leads now?"
- PROBLEM (P): Surface dissatisfaction. "What happens after hours?"
- IMPLICATION (I): Most powerful. "What does it cost you if leads sit overnight?"
- NEED-PAYOFF (N): Get THEM to say the value. "What changes if you catch leads instantly?"
""",
    "rag:sales:objections": """
═══ OBJECTION HANDLING (DIAGNOSTIC) ═══
1. ACKNOWLEDGE: "Fair point." "I get that."
2. DIAGNOSE: "Is it the timing or the cost that's the main thing?"
3. REFRAME: 
   - Too expensive? Reframe against ad spend loss.
   - Partner check? Suggest a call with both.
   - Not interested? Ask ONE follow-up: "What made you fill in the form?"
""",
    "rag:sales:text_selling": """
═══ TEXT-SPECIFIC RULES ═══
- PRIMACY/RECENCY: Questions go LAST.
- PROCESSING FLUENCY: Simple messages = trust.
- RECIPROCITY: Share a genuine fact about us to get honesty back.
- SILENCE IS CONFIDENCE: Don't double-message quickly.
""",
    "rag:sales:signals": """
═══ BUYER SIGNAL REFERENCE ═══
- BUYING: Asking about pricing, process, or booking. Move fast.
- HIGH: Engaged, asking questions. Match energy.
- LOW: Short, flat. Use pattern interrupt. Don't push call yet.
""",
    "rag:sales:closing": """
═══ NATURAL CLOSING ═══
- ASSUMPTIVE: "The team can walk you through this. Want me to send the link?"
- SUMMARY CLOSE: Reflect their words back before suggesting the call.
- THE CALL IS THE DEMO: Focus only on getting them to the call.
- DON'T OVERSELL: Once they say yes, stop selling. Just send link.
""",
    "rag:sales:voss": """
═══ TACTICAL EMPATHY (CHRIS VOSS) ═══
- LABELLING: "Seems like you've been burned by this before."
- MIRRORING: Repeat 2-3 words.
- CALIBRATED QUESTIONS: "How would you see this fitting into your current setup?"
- ACCUSATION AUDIT: "You probably think this is another robotic chatbot."
""",
    "rag:sales:personality": """
═══ PERSONALITY MATCHING ═══
- DRIVER: Short, direct. No fluff.
- ANALYTICAL: Thorough, specific. Logical.
- EXPRESSIVE: Warm, chatty. Rapport first.
- AMIABLE: Low pressure. Safe steps.
"""
}

async def seed():
    print("🚀 Seeding Sales Intelligence into Redis...")
    for key, content in SALES_INTEL.items():
        await redis_client.redis.set(key, content.strip(), ex=86400 * 30)
        print(f"✅ Seeded {key}")
    print("✨ Seeding complete!")

if __name__ == "__main__":
    asyncio.run(seed())
