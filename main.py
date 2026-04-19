import logging
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.webhook import router as webhook_router
from app.outbound import router as outbound_router
from app.calcom import router as calcom_router
from app.training_api import router as training_router
from app.dashboard import router as dashboard_router
from app.config import settings
import sentry_sdk
import os

# 1. Initialize Sentry (Operational Excellence)
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
        environment=settings.ENVIRONMENT
    )

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stdout
)
from app.conversation_library import load_conversation_library
from app.redis_client import redis_client

logger = logging.getLogger(__name__)
logger.info("Application starting...")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    logger.info("Running startup tasks...")
    import asyncio
    from app.conversation_library import load_conversation_library
    from app.redis_client import redis_client
    from app.baileys_bridge import baileys_bridge
    from app.client_manager import client_manager

    await load_conversation_library(redis_client.redis)
    
    # Start Baileys Bridge for direct WhatsApp integration
    asyncio.create_task(baileys_bridge.start())
    logger.info("OK: Baileys Bridge listener launched")
    
    # Auto-initialize all clients (Module 6)
    asyncio.create_task(client_manager.init_all_clients())
    logger.info("OK: Multi-session client initialization triggered")

    yield

    # SHUTDOWN
    from app.baileys_bridge import baileys_bridge
    logger.info("Stopping Baileys Bridge...")
    await baileys_bridge.stop()

app = FastAPI(title="Markeye WhatsApp AI Agent — Mark", version="2.0.0", lifespan=lifespan)

# Enable CORS for local development & production (Fix 1)
origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

from app.middleware import TelemetryMiddleware
app.add_middleware(TelemetryMiddleware)

from fastapi.staticfiles import StaticFiles
# Try to serve the premium dashboard if built, fallback to simple dashboard
dist_path = "../after5-agent-front/dist"
if os.path.exists(dist_path):
    app.mount("/admin", StaticFiles(directory=dist_path, html=True), name="admin")
else:
    app.mount("/admin", StaticFiles(directory="dashboard", html=True), name="admin")

app.include_router(webhook_router)
app.include_router(outbound_router)
app.include_router(calcom_router)
app.include_router(training_router)
app.include_router(dashboard_router)

@app.get("/")
async def health():
    return {"status": "Mark AI SDR is running", "version": "2.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# Global application startTime
import time
start_time_global = time.time()

@app.get("/metrics")
async def get_metrics():
    """Returns real-time operational stats."""
    metrics = await redis_client.get_metrics()
    uptime_seconds = int(time.time() - start_time_global)
    
    return {
        "uptime_seconds": uptime_seconds,
        "total_messages_processed": metrics.get("requests_total", 0),
        "total_llm_calls": metrics.get("total_llm_calls", 0),
        "llm_provider_usage": {
            "groq": metrics.get("llm_provider:groq", 0),
            "gemini": metrics.get("llm_provider:gemini", 0),
            "cerebras": metrics.get("llm_provider:cerebras", 0),
        },
        "errors_total": metrics.get("errors_http", 0),
        "estimated_token_burn": metrics.get("total_tokens", 0)
    }
