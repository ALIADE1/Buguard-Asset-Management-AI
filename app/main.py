"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import engine, Base

# ── Logging ────────────────────────────────────────────
logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


# ── Lifespan — create tables on startup ────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create all tables (dev convenience) and dispose engine on shutdown."""
    # Import models so Base.metadata knows about them
    import app.models.asset  # noqa: F401
    import app.models.relationship  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created / verified.")
    yield
    await engine.dispose()
    logger.info("Database engine disposed.")


# ── App ────────────────────────────────────────────────
app = FastAPI(
    title="Buguard Asset Management System",
    description="AI-powered Attack Surface Management — asset inventory, risk scoring, and natural-language querying.",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Register routers (imported lazily to avoid circular deps) ──
from app.routers import assets as assets_router  # noqa: E402
from app.routers import ai as ai_router  # noqa: E402

app.include_router(assets_router.router, prefix="/api/v1/assets", tags=["Assets"])
app.include_router(ai_router.router, prefix="/api/v1/ai", tags=["AI / LangChain"])


@app.get("/health", tags=["Health"])
async def health_check():
    """Simple liveness probe."""
    return {"status": "healthy"}
