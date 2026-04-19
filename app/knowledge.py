import os
from typing import List
from openai import AsyncOpenAI
from app.supabase_client import supabase_client
from app.config import settings

# Initialize OpenAI client for embeddings
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

async def get_query_embedding(text: str) -> List[float]:
    """Generates embedding for the search query."""
    text = text.replace("\n", " ")
    response = await openai_client.embeddings.create(
        input=[text],
        model="text-embedding-3-small"
    )
    return response.data[0].embedding

import logging
logger = logging.getLogger(__name__)

async def retrieve_knowledge(query: str, threshold: float = 0.4, limit: int = 3) -> str:
    """Searches the knowledge base and returns concatenated context."""
    try:
        # 1. Generate embedding for the user's query
        query_vec = await get_query_embedding(query)

        # 2. Call the RPC function we created in Supabase
        client = await supabase_client.get_client()
        result = await client.rpc(
            "match_knowledge",
            {
                "query_embedding": query_vec,
                "match_threshold": threshold,
                "match_count": limit
            }
        ).execute()

        if not result.data:
            return ""

        # 3. Format the results into a single context string
        context_parts = []
        for item in result.data:
            content = item.get("content", "").strip()
            if content:
                context_parts.append(f"--- INFO SOURCE ---\n{content}")
        
        return "\n\n".join(context_parts)

    except Exception as e:
        logger.error(f"[Knowledge Base Error] {e}")
        return ""
