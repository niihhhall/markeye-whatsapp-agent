from fastapi import APIRouter, HTTPException
from typing import List
from app.client_manager import client_manager
from app.tracker import MarkTracker

router = APIRouter(prefix="/dashboard")
tracker = MarkTracker()

@router.get("/clients")
async def list_clients():
    return await client_manager.list_clients()

@router.post("/clients")
async def create_client(data: dict):
    return await client_manager.create_client(data)

@router.get("/clients/{client_id}")
async def get_client(client_id: str):
    client = await client_manager.get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client

@router.put("/clients/{client_id}")
async def update_client(client_id: str, data: dict):
    return await client_manager.update_client(client_id, data)

@router.get("/clients/{client_id}/leads")
async def get_client_leads(client_id: str):
    return await tracker.get_all_leads(client_id=client_id)

@router.get("/clients/{client_id}/stats")
async def get_client_stats(client_id: str):
    # This is a placeholder for actual statistics logic
    # In a real app, this would perform aggregations in Supabase
    leads = await tracker.get_all_leads(client_id=client_id)
    total_leads = len(leads)
    hot_leads = len([l for l in leads if l.get("temperature") == "Hot"])
    return {
        "total_leads": total_leads,
        "hot_leads": hot_leads,
        "conversion_rate": (hot_leads / total_leads * 100) if total_leads > 0 else 0
    }
