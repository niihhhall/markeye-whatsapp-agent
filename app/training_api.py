import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from app.config import settings
from app.supabase_client import supabase_client
from app.training_utils import messages_to_training_format, get_training_stats, validate_jsonl_line
from app.client_manager import client_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/training", tags=["training"])

@router.post("/compile")
async def compile_training_data():
    """
    Finds finished leads (booked/lost or stale >24h) and compiles 
    their message history into the conversations table for training.
    """
    try:
        client = await supabase_client.get_client()
        
        # 1. Identify "finished" leads or stale ones
        # For simplicity, we fetch leads with status 'booked', 'lost' or not updated for 24h
        # and that don't have a record in 'conversations' table yet.
        
        # We'll use a subquery approach via Postgrest
        # For now, let's fetch leads that might be eligible
        query = client.table("leads").select("id, phone, client_id, outcome, updated_at")
        res = await query.execute()
        leads = res.data or []
        
        compiled_count = 0
        now = datetime.now(timezone.utc)
        
        for lead in leads:
            lead_id = lead["id"]
            
            # Check if already compiled
            check_res = await client.table("conversations").select("id").eq("lead_id", lead_id).execute()
            if check_res.data:
                continue
                
            # Eligibility check
            is_finished = lead["outcome"].lower() in ["booked", "lost", "closed"]
            last_updated = datetime.fromisoformat(lead["updated_at"].replace("Z", "+00:00"))
            is_stale = (now - last_updated).total_seconds() > 86400 # 24 hours
            
            if is_finished or is_stale:
                # Compile messages
                msg_res = await client.table("messages").select("*").eq("lead_id", lead_id).order("created_at").execute()
                messages = msg_res.data or []
                
                if not messages:
                    continue
                    
                # Get client system prompt
                client_id = lead.get("client_id")
                system_prompt = ""
                if client_id:
                    cfg = await client_manager.get_client_by_id(client_id)
                    system_prompt = cfg.get("system_prompt", "") if cfg else ""
                
                if not system_prompt:
                    prompt_path = os.path.join(os.getcwd(), "prompts", "system_prompt.txt")
                    if os.path.exists(prompt_path):
                        with open(prompt_path, "r", encoding="utf-8") as f:
                            system_prompt = f.read()

                # Format for training
                training_data = messages_to_training_format(messages, system_prompt)
                if not training_data:
                    continue
                
                # Insert into conversations
                await client.table("conversations").insert({
                    "client_id": client_id,
                    "lead_id": lead_id,
                    "messages_jsonl": json.dumps(training_data),
                    "quality_label": None
                }).execute()
                compiled_count += 1
                
        return {"status": "ok", "compiled": compiled_count}
    except Exception as e:
        logger.error(f"[Training] Compilation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/label")
async def label_conversation(payload: Dict[str, Any]):
    """Label a specific conversation as good, bad, or neutral."""
    cid = payload.get("conversation_id")
    label = payload.get("quality_label")
    
    if label not in ["good", "bad", "neutral"]:
        raise HTTPException(status_code=400, detail="Invalid quality_label")
        
    try:
        client = await supabase_client.get_client()
        res = await client.table("conversations").update({"quality_label": label}).eq("id", cid).execute()
        return {"updated": True, "conversation_id": cid, "label": label}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/label/bulk")
async def bulk_label_conversations(payload: Dict[str, Any]):
    """Update multiple conversations at once."""
    cids = payload.get("conversation_ids", [])
    label = payload.get("quality_label")
    
    if label not in ["good", "bad", "neutral"]:
        raise HTTPException(status_code=400, detail="Invalid quality_label")
        
    try:
        client = await supabase_client.get_client()
        # Postgrest 'in' query
        res = await client.table("conversations").update({"quality_label": label}).in_("id", cids).execute()
        return {"updated": len(res.data) if res.data else 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_stats():
    """Return counts and health metrics for the training dataset."""
    try:
        client = await supabase_client.get_client()
        res = await client.table("conversations").select("quality_label, exported").execute()
        data = res.data or []
        
        stats = {
            "total_conversations": len(data),
            "unlabeled": len([d for d in data if d["quality_label"] is None]),
            "good": len([d for d in data if d["quality_label"] == "good"]),
            "bad": len([d for d in data if d["quality_label"] == "bad"]),
            "neutral": len([d for d in data if d["quality_label"] == "neutral"]),
            "exported": len([d for d in data if d["exported"]]),
            "not_exported": len([d for d in data if not d["exported"]])
        }
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conversations")
async def list_conversations(
    label: Optional[str] = None, 
    exported: Optional[bool] = None, 
    client_id: Optional[str] = None, 
    limit: int = 50
):
    """List conversations for review (no full JSONL body)."""
    try:
        client = await supabase_client.get_client()
        query = client.table("conversations").select("id, client_id, lead_id, quality_label, exported, created_at, messages_jsonl")
        
        if label: query = query.eq("quality_label", label)
        if exported is not None: query = query.eq("exported", exported)
        if client_id: query = query.eq("client_id", client_id)
        
        res = await query.order("created_at", desc=True).limit(limit).execute()
        
        results = []
        for row in (res.data or []):
            try:
                # Calculate turn count from JSONL without returning the whole blob
                msg_data = json.loads(row["messages_jsonl"])
                turn_count = len(msg_data.get("messages", []))
            except:
                turn_count = 0
                
            results.append({
                "id": row["id"],
                "client_id": row["client_id"],
                "lead_id": row["lead_id"],
                "quality_label": row["quality_label"],
                "exported": row["exported"],
                "created_at": row["created_at"],
                "message_count": turn_count
            })
            
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conversations/{conversation_id}")
async def get_conversation_detail(conversation_id: str):
    """Get full conversation for labeling review."""
    try:
        client = await supabase_client.get_client()
        res = await client.table("conversations").select("*").eq("id", conversation_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        row = res.data[0]
        row["messages"] = json.loads(row["messages_jsonl"])
        return row
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/export")
async def export_data(client_id: Optional[str] = None):
    """Generates JSONL export for all 'good' non-exported conversations."""
    try:
        db = await supabase_client.get_client()
        query = db.table("conversations").select("*").eq("quality_label", "good").eq("exported", False)
        if client_id:
            query = query.eq("client_id", client_id)
            
        res = await query.execute()
        records = res.data or []
        
        if not records:
            raise HTTPException(status_code=404, detail="No 'good' unlabeled data to export")
            
        lines = []
        cids = []
        for rec in records:
            lines.append(rec["messages_jsonl"])
            cids.append(rec["id"])
            
        # Mark as exported
        await db.table("conversations").update({"exported": True}).in_("id", cids).execute()
        
        content = "\n".join(lines) + "\n"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        
        return StreamingResponse(
            iter([content]),
            media_type="application/jsonl",
            headers={
                "Content-Disposition": f"attachment; filename=markeye_export_{timestamp}.jsonl",
                "X-Export-Count": str(len(records))
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/export/preview")
async def export_preview(client_id: Optional[str] = None):
    """Preview first 5 conversations for export."""
    try:
        db = await supabase_client.get_client()
        query = db.table("conversations").select("*").eq("quality_label", "good").eq("exported", False)
        if client_id:
            query = query.eq("client_id", client_id)
            
        res = await query.order("created_at").limit(5).execute()
        data = []
        for row in (res.data or []):
            data.append(json.loads(row["messages_jsonl"]))
            
        return {"count": len(res.data), "preview": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
