"""
MedNexus Database Module
-------------------------
Establishes async PostgreSQL connections for both databases:
  1. Tenant database     — hospital operational data
  2. Platform database   — LATLOMP platform data

Uses SQLAlchemy's async engine with asyncpg driver.
All database sessions are managed through dependency injection.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator

from app.config import settings


# ── Tenant Database (Hospital Data) ──────────────────────────────────────────

# The engine is the raw connection to PostgreSQL.
# pool_pre_ping=True: tests the connection before using it (handles dropped connections)
# echo=True in development so you can see SQL queries in the terminal
tenant_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    echo=settings.DEBUG,  # Logs SQL queries when DEBUG=True
    pool_size=10,          # Keep 10 connections ready
    max_overflow=20,       # Allow up to 20 additional connections under load
)

# Session factory for tenant database
# autocommit=False: we control when transactions are committed
# autoflush=False:  we control when changes are flushed to the DB
TenantSessionLocal = async_sessionmaker(
    bind=tenant_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Keep objects accessible after commit
)


# ── Platform Database (LATLOMP Data) ─────────────────────────────────────────

platform_engine = create_async_engine(
    settings.PLATFORM_DATABASE_URL,
    pool_pre_ping=True,
    echo=settings.DEBUG,
    pool_size=5,    # Platform DB gets less traffic than tenant DB
    max_overflow=10,
)

PlatformSessionLocal = async_sessionmaker(
    bind=platform_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


# ── Base Model Classes ────────────────────────────────────────────────────────

class TenantBase(DeclarativeBase):
    """
    Base class for all tenant (hospital) database models.
    All models in app/models/tenant/ should inherit from this.
    """
    pass


class PlatformBase(DeclarativeBase):
    """
    Base class for all platform database models.
    All models in app/models/platform/ should inherit from this.
    """
    pass


# ── Dependency Functions ──────────────────────────────────────────────────────
# These are injected into FastAPI route handlers.
# FastAPI calls them automatically and handles cleanup.

async def get_tenant_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Provides a database session for tenant (hospital) operations.
    
    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_tenant_db)):
            ...
    
    The session automatically closes after the request completes,
    even if an error occurs.
    """
    async with TenantSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_platform_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Provides a database session for platform (LATLOMP) operations.
    
    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_platform_db)):
            ...
    """
    async with PlatformSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()