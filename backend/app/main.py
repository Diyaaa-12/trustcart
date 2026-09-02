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
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://frontend:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request ID middleware ────────────────────────────────────────────────────
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
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
@app.get("/health", tags=["meta"])
async def health_check() -> dict:
    return {
        "status": "ok",
        "env": settings.APP_ENV,
        "mock_checkout": settings.mock_checkout,
        "llm_provider": settings.LLM_PROVIDER,
    }
