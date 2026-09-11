"""FastAPI application entrypoint.

Deployed on Render as:
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import __version__
from .countries import registry_as_dicts
from .schemas import CountryOut, HealthOut
from .config import get_settings
from .db import get_engine, get_session_factory, init_db
from .routers import boq, indices, variance


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create any missing tables on boot. Safe and idempotent; it also means a
    # fresh Render PostgreSQL instance is usable before the first ETL run.
    init_db()
    yield


settings = get_settings()

app = FastAPI(
    title="shouldcost",
    description=(
        "Should-cost BoQ benchmarking for Singapore and India. SMM2 / IS 1200 classification, "
        "index adjustment, variance, waterfall and sensitivity reporting."
    ),
    version=__version__,
    lifespan=lifespan,
)

# CORS: an explicit allowlist only. A wildcard is never used, in any environment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins(),
    allow_origin_regex=settings.cors_allow_origin_regex(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    # Without this the browser cannot read the filename of a downloaded export.
    expose_headers=["Content-Disposition"],
)

app.include_router(boq.router)
app.include_router(variance.router)
app.include_router(indices.router)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "service": "shouldcost-sg",
        "version": __version__,
        "environment": settings.environment,
        "docs": "/docs",
        "health": "/api/healthz",
        "markets": ["SG", "IN"],
        "data_notice": (
            "Bundled index and benchmark data for BOTH markets are SYNTHETIC PLACEHOLDERS "
            "(is_placeholder = true). Do not use for a real tender decision."
        ),
    }


@app.get("/api/healthz", response_model=HealthOut, tags=["health"])
def healthz() -> HealthOut:
    """Liveness + database connectivity probe. Used as the Render health check."""
    db_state = "connected"
    try:
        session = get_session_factory()()
        try:
            session.execute(text("SELECT 1"))
        finally:
            session.close()
    except Exception as exc:  # pragma: no cover - depends on infrastructure
        db_state = f"error: {type(exc).__name__}"
    return HealthOut(
        status="ok" if db_state == "connected" else "degraded",
        db=db_state,
        environment=settings.environment,
    )


@app.get("/api/countries", response_model=list[CountryOut], tags=["reference"])
def list_countries() -> list[CountryOut]:
    """Supported markets, their currency, measurement standard and credible sources."""
    return [CountryOut(**entry) for entry in registry_as_dicts()]


@app.get("/api/config", tags=["health"])
def public_config() -> dict:
    """Non-secret runtime facts the UI can display as an environment banner."""
    dialect = get_engine().dialect.name
    return {
        "environment": settings.environment,
        "database_dialect": dialect,
        "cors_allow_origins": settings.cors_allow_origins(),
        "cors_allow_origin_regex": settings.cors_allow_origin_regex(),
        "frontend_url_configured": bool(settings.frontend_url),
    }
