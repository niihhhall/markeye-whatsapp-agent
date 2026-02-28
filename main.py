from fastapi import FastAPI
from app.webhook import router as webhook_router
from app.outbound import router as outbound_router
from app.config import settings

app = FastAPI(title="After5 WhatsApp AI Agent", version="1.0.0")

app.include_router(webhook_router)
app.include_router(outbound_router)

@app.get("/")
async def health():
    return {"status": "After5 Agent is running", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}
