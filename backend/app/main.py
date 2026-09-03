"""
FastAPI application entry point.

Middleware stack (inner → outer):
  1. Request ID injection (X-Request-ID header, bound to structlog context)
  2. CORS

Lifespan:
  - Create DB tables
  - Seed catalog (idempotent)
"""
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import create_tables
from app.logging_config import setup_logging
from app.routers import audit, cart, catalog, checkout, proposals
from app.seed import seed_products

setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("TrustCart starting up", extra={"env": settings.APP_ENV})
    await create_tables()
    await seed_products()
    logger.info(
        "Startup complete",
        extra={"mock_checkout": settings.mock_checkout, "llm_provider": settings.LLM_PROVIDER},
    )
    yield
    logger.info("TrustCart shutting down")


app = FastAPI(
    title="TrustCart API",
    description="Bounded, auditable merchant upsell/cross-sell agent",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────────────
def _get_cors_origins() -> list[str]:
    raw = settings.CORS_ORIGINS
    if isinstance(raw, str):
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return list(raw)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request ID middleware ────────────────────────────────────────────────────
@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Any) -> Response:
    """Attach a request ID to every request for end-to-end tracing."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    structlog.contextvars.clear_contextvars()
    return response


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(catalog.router, prefix="/api")
app.include_router(cart.router, prefix="/api")
app.include_router(proposals.router, prefix="/api")
app.include_router(checkout.router, prefix="/api")
app.include_router(audit.router, prefix="/api")


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"], summary="Health check probe")
@app.get("/healthz", tags=["meta"], summary="Liveness/readiness probe")
async def health_check() -> dict:
    return {
        "status": "ok",
        "env": settings.APP_ENV,
        "mock_checkout": settings.mock_checkout,
        "llm_provider": settings.LLM_PROVIDER,
    }
