import time
import json
import logging
import sys
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.redis_client import redis_client

logger = logging.getLogger("app.telemetry")

class TelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Log HTTP Request
        log_data = {
            "type": "http_request",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms
        }
        
        logger.info(json.dumps(log_data))
        
        # Increment metrics
        if response.status_code >= 400:
            await redis_client.inc_metric("errors_http")
        else:
            await redis_client.inc_metric("requests_total")
            
        return response

def log_llm_call(provider: str, model: str, latency_ms: int, tokens_in: int, tokens_out: int, success: bool, client_id: str = "unknown"):
    """Helper to log LLM calls in a structured format."""
    log_data = {
        "type": "llm_call",
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "success": success,
        "client_id": client_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    logger.info(json.dumps(log_data))
