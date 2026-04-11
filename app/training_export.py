import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

from app.supabase_client import supabase_client

async def export_training_data(format: str = "openai") -> str:
    """
    Export worthy training data from Supabase.
    Prioritizes human-reviewed data with high manual scores.
    """
    try:
        # Fetch high-quality data from Supabase
        # We take anything with manual_score >= 80 OR (score >= 80 and not reviewed)
        response = await supabase_client.table("training_data") \
            .select("*") \
            .order("is_reviewed", desc=True) \
            .order("manual_score", desc=True) \
            .order("score", desc=True) \
            .execute()
        
        records = response.data
        if not records:
            return "No training data found in Supabase."

        export_dir = os.path.join(os.getcwd(), "exports")
        os.makedirs(export_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"markeye_training_{timestamp}.jsonl"
        filepath = os.path.join(export_dir, filename)

        count = 0
        with open(filepath, "w", encoding="utf-8") as f:
            for rec in records:
                # Quality filter for auto-scored ones
                if not rec.get("is_reviewed") and rec.get("score", 0) < 80:
                    continue
                
                # Quality filter for manually reviewed ones
                if rec.get("is_reviewed") and rec.get("manual_score", 0) < 70:
                    continue

                history = rec["history"]
                
                if format == "openai":
                    jsonl_row = _format_openai(history)
                elif format == "anthropic":
                    jsonl_row = _format_anthropic(history)
                else:
                    continue

                f.write(json.dumps(jsonl_row) + "\n")
                count += 1

        logger.info(f"Exported {count} training examples to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Export error: {e}")
        return str(e)


def _format_openai(history: List[Dict[str, str]]) -> Dict[str, Any]:
    """Format for OpenAI fine-tuning."""
    messages = []
    
    # Optional: system message should be included if using fine-tuning (usually)
    # messages.append({"role": "system", "content": "You are Mark, Markeye's AI sales agent..."})
    
    for msg in history:
        role = "assistant" if msg["role"] == "assistant" else "user"
        content = msg["content"]
        
        # Strip chunking symbols for training if needed, 
        # or KEEP them to teach chunking. User generally wants to keep them.
        messages.append({"role": role, "content": content})
        
    return {"messages": messages}


def _format_anthropic(history: List[Dict[str, str]]) -> Dict[str, Any]:
    """Format for Anthropic fine-tuning (simplified placeholder)."""
    # Anthropic uses a different format, usually message pairs or similar.
    return {"history": history}
