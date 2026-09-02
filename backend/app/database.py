"""
Async SQLAlchemy engine, session factory, and declarative base.
All models import Base from here to share metadata.
"""
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


async def create_tables() -> None:
    """Create all tables on startup (dev/demo use; use Alembic for prod)."""
    # Import models so they register with Base.metadata
    import app.models.cart  # noqa: F401
    import app.models.product  # noqa: F401
    import app.models.proposal  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Ensure trust_score column exists if table was already created in PostgreSQL
        try:
            await conn.execute(
                text(
                    "ALTER TABLE cart_sessions ADD COLUMN IF NOT EXISTS "
                    "trust_score NUMERIC(6, 2) NOT NULL DEFAULT 100"
                )
            )
        except Exception:  # noqa: S110
            pass

        try:
            await conn.execute(
                text(
                    "ALTER TABLE cart_sessions ADD COLUMN IF NOT EXISTS "
                    "mandate_payload JSON"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE cart_sessions ADD COLUMN IF NOT EXISTS "
                    "mandate_signature VARCHAR(128)"
                )
            )
        except Exception:  # noqa: S110
            pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a DB session per request."""
    async with AsyncSessionLocal() as session:
        yield session
