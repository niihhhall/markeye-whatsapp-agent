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
    leads = await tracker.get_all_leads(client_id=client_id)
    total_leads = len(leads)
    hot_leads = len([l for l in leads if l.get("temperature") == "Hot"])
    return {
        "total_leads": total_leads,
        "hot_leads": hot_leads,
        "conversion_rate": (hot_leads / total_leads * 100) if total_leads > 0 else 0
    }

@router.get("/sessions")
async def list_baileys_sessions():
    """Fetch all active Baileys sessions from the JS service."""
    import httpx
    from app.config import settings
    url = f"{settings.BAILEYS_API_URL or 'http://localhost:3001'}/sessions/status"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=5.0)
            return res.json()
    except Exception as e:
        return {"error": str(e), "status": "service_down"}

@router.post("/sessions/start")
async def start_session(data: dict):
    """Manually trigger a session start for a client."""
    from app.client_manager import client_manager
    session_id = data.get("sessionId")
    if not session_id:
        raise HTTPException(status_code=400, detail="sessionId required")
    
    # We trigger the same logic as the auto-load
    import httpx
    from app.config import settings
    url = f"{settings.BAILEYS_API_URL or 'http://localhost:3001'}/sessions/start"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=data, timeout=5.0)
            return res.json()
    except Exception as e:
        return {"error": str(e)}

@router.get("/sessions/{client_id}/qr")
async def get_session_qr(client_id: str):
