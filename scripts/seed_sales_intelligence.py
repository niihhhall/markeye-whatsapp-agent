import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.redis_client import redis_client

SALES_DOC = {
    "rag:sales:psychology": """????????? SALES PSYCHOLOGY ??? EMOTION & CERTAINTY ?????????
- People decide with emotion (PAIN) and justify with logic.
- LOSS AVERSION: Frame around what they're CURRENTLY LOSING (ad spend going cold).
- THE IKEA EFFECT: Ask questions so THEY articulate the problem/gap.
- COGNITIVE LOAD: One thought per message. Brevity respects attention.""",

    "rag:sales:spin": """????????? SPIN FRAMEWORK ??? QUESTIONING ?????????
- SITUATION (S): Get basics fast. Don't linger.
- PROBLEM (P): Surface dissatisfaction/gaps.
- IMPLICATION (I): MOST POWERFUL. Explore consequences of problems. Re-frame small problems as big costs.
- NEED-PAYOFF (N): Get THEM to say the value of the solution.""",

    "rag:sales:objections": """????????? OBJECTION HANDLING ??? ACKNOWLEDGE & DIAGNOSE ?????????
- Never argue. Always acknowledge first ("Fair point", "Makes sense").
- DIAGNOSE: Ask why they feel that way before reframing.
- REFRAME: Shift from price to value/cost of delay.
- "Too expensive" -> Reframe against ad spend/VA overhead.
- "Not interested" -> ONE follow-up ("Why did you fill the form?").""",

    "rag:sales:text_selling": """????????? TEXT SELLING RULES ?????????
- PRIMACY & RECENCY: Put important points first or last.
- Processing Fluency: Simple words, clear structure = trust.
- Reciprocity: Be honest/vulnerable to invite honesty.
- Silence is Confidence: Give them space between replies.""",

    "rag:sales:signals": """????????? BUYER SIGNALS & PERSONALITY ?????????
- DRIVER: Wants results, efficiency. Keep messages short and direct.
- ANALYTICAL: Wants depth. Be thorough and logical.
- EXPRESSIVE: Wants connection. Be warm and casual.
- AMIABLE: Wants safety. Use low pressure, emphasize no-obligation.""",

    "rag:sales:closing": """????????? CLOSING ??? NATURAL PROGRESSION ?????????
- ASSUMPTIVE: The team can show you X... want the link?
- SUMMARY CLOSE: Reflect their words back before suggesting call.
- The Call is a Taste: The call IS the demo.
- When NOT to close: If they said yes, stop selling. If they're not ready, leave door open.""",
}

async def seed():
    print("???? Seeding Sales Intelligence RAG to Redis...")
    for key, content in SALES_DOC.items():
        await redis_client.set(key, content, ex=86400 * 30) # 30 days
        print(f"??? Seeded {key}")
    print("??? Finished seeding.")

if __name__ == "__main__":
    asyncio.run(seed())
