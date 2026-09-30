"""
MedNexus — Main Application Entry Point
-----------------------------------------
This file creates and configures the FastAPI application instance.
Everything is registered here: routers, middleware, exception handlers.

To run the server:
    uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.config import settings
from app.core.exceptions import MedNexusException
from app.core.rate_limiter import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# ── Import Routers ────────────────────────────────────────────────────────────

# Platform admin routers (LATLOMP owner dashboard)
from app.routers.platform import auth as platform_auth
from app.routers.platform import hospitals as platform_hospitals
from app.routers.platform import subscriptions as platform_subscriptions
from app.routers.platform import analytics as platform_analytics
from app.routers.platform import support_tickets as platform_support_tickets
from app.routers.platform import announcements as platform_announcements
from app.routers.platform import audit_log as platform_audit_log

# Hospital routers (tenant-facing)
from app.routers.hospital import auth as hospital_auth
from app.routers.hospital import registration as hospital_registration
from app.routers.hospital import departments as hospital_departments
from app.routers.hospital import staff as hospital_staff
from app.routers.hospital import patients as hospital_patients
from app.routers.hospital import appointments as hospital_appointments
from app.routers.hospital import medical_records as hospital_medical_records
from app.routers.hospital import prescriptions as hospital_prescriptions
from app.routers.hospital import lab_orders as hospital_lab_orders
from app.routers.hospital import pharmacy as hospital_pharmacy
from app.routers.hospital import billing as hospital_billing
from app.routers.hospital import support as hospital_support
from app.routers.hospital import announcements as hospital_announcements
from app.routers.hospital import documents as hospital_documents
from app.routers.hospital import communications as hospital_communications
from app.routers.hospital import notifications as hospital_notifications


# ── Lifespan (Startup / Shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    print(f"   Environment: {settings.ENVIRONMENT}")
    print(f"   Debug mode:  {settings.DEBUG}")
    print(f"   API prefix:  {settings.API_V1_PREFIX}")

    await _check_database_connections()

    yield

    print(f"🛑 Shutting down {settings.APP_NAME}")

    from app.database import tenant_engine, platform_engine
    await tenant_engine.dispose()
    await platform_engine.dispose()
    print("   Database connections closed.")


async def _check_database_connections():
    from sqlalchemy import text
    from app.database import tenant_engine, platform_engine

    try:
        async with tenant_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("   ✅ Tenant database connected (mednexus)")
    except Exception as e:
        print(f"   ❌ Tenant database connection failed: {e}")
        print("      Check DATABASE_URL in your .env file.")
        raise RuntimeError(f"Cannot connect to tenant database: {e}")

    try:
        async with platform_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("   ✅ Platform database connected (mednexus_platform)")
    except Exception as e:
        print(f"   ❌ Platform database connection failed: {e}")
        print("      Check PLATFORM_DATABASE_URL in your .env file.")
        raise RuntimeError(f"Cannot connect to platform database: {e}")


# ── Create FastAPI Application ────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Global Healthcare Ecosystem — Digital Infrastructure for Hospitals",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# ── Rate Limiter Setup ────────────────────────────────────────────────────────
# Register limiter on app state so slowapi can find it, then add the
# middleware that intercepts requests and the exception handler for 429s.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ── CORS Configuration ────────────────────────────────────────────────────────
if settings.ENVIRONMENT == "development":
    allowed_origins = ["*"]
else:
    allowed_origins = [
        "https://mednexus.com",
        "https://www.mednexus.com",
        "https://app.mednexus.com",
        "https://admin.mednexus.com",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ProxyFix for Production ───────────────────────────────────────────────────
# When deployed behind Railway/Render's reverse proxy, the real client IP
# is in X-Forwarded-For, not request.client.host. ProxyFix makes slowapi
# extract the correct IP so rate limits apply per real client, not per proxy.
if settings.ENVIRONMENT != "development":
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.add_middleware(
        type(
            "ProxyFixMiddleware",
            (),
            {
                "__init__": lambda self, app: setattr(self, "app", ProxyFix(app, x_for=1)),
                "__call__": lambda self, scope, receive, send: self.app(scope, receive, send),
            },
        )
    )


# ── Global Exception Handler ──────────────────────────────────────────────────
@app.exception_handler(MedNexusException)
async def mednexus_exception_handler(request: Request, exc: MedNexusException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
            }
        }
    )


# ── System Endpoints ──────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    from sqlalchemy import text
    from app.database import tenant_engine, platform_engine

    db_status = {}

    try:
        async with tenant_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status["tenant_db"] = "healthy"
    except Exception:
        db_status["tenant_db"] = "unreachable"

    try:
        async with platform_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status["platform_db"] = "healthy"
    except Exception:
        db_status["platform_db"] = "unreachable"

    overall = "healthy" if all(
        v == "healthy" for v in db_status.values()
    ) else "degraded"

    return {
        "success": True,
        "data": {
            "status": overall,
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "databases": db_status,
        }
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "success": True,
        "data": {
            "message": f"Welcome to {settings.APP_NAME} API",
            "version": settings.APP_VERSION,
            "docs": "/docs",
        }
    }


# ── Register Routers ──────────────────────────────────────────────────────────

# ── Platform Admin Routes ─────────────────────────────────────────────────────
app.include_router(
    platform_auth.router,
    prefix=f"{settings.API_V1_PREFIX}/platform/auth",
    tags=["Platform — Authentication"],
)

app.include_router(
    platform_hospitals.router,
    prefix=f"{settings.API_V1_PREFIX}/platform/hospitals",
    tags=["Platform — Hospital Management"],
)

app.include_router(
    platform_subscriptions.router,
    prefix=f"{settings.API_V1_PREFIX}/platform/hospitals",
    tags=["Platform — Subscriptions"],
)

app.include_router(
    platform_analytics.router,
    prefix=f"{settings.API_V1_PREFIX}/platform/analytics",
    tags=["Platform — Analytics"],
)

app.include_router(
    platform_support_tickets.router,
    prefix=f"{settings.API_V1_PREFIX}/platform/support-tickets",
    tags=["Platform — Support Tickets"],
)

app.include_router(
    platform_announcements.router,
    prefix=f"{settings.API_V1_PREFIX}/platform/announcements",
    tags=["Platform — Announcements"],
)

app.include_router(
    platform_audit_log.router,
    prefix=f"{settings.API_V1_PREFIX}/platform/audit-log",
    tags=["Platform — Audit Log"],
)

# ── Hospital Routes ───────────────────────────────────────────────────────────
app.include_router(
    hospital_auth.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/auth",
    tags=["Hospital — Authentication"],
)

app.include_router(
    hospital_registration.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/registration",
    tags=["Hospital — Registration"],
)

app.include_router(
    hospital_departments.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/departments",
    tags=["Hospital — Departments"],
)

app.include_router(
    hospital_staff.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/staff",
    tags=["Hospital — Staff Management"],
)

app.include_router(
    hospital_patients.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/patients",
    tags=["Hospital — Patient Management"],
)

app.include_router(
    hospital_appointments.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/appointments",
    tags=["Hospital — Appointments"],
)

app.include_router(
    hospital_medical_records.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/medical-records",
    tags=["Hospital — Medical Records"],
)

app.include_router(
    hospital_prescriptions.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/prescriptions",
    tags=["Hospital — Prescriptions"],
)

app.include_router(
    hospital_lab_orders.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/lab-orders",
    tags=["Hospital — Laboratory"],
)

app.include_router(
    hospital_pharmacy.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/pharmacy",
    tags=["Hospital — Pharmacy Inventory"],
)

app.include_router(
    hospital_billing.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/billing",
    tags=["Hospital — Billing"],
)

app.include_router(
    hospital_support.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/support-tickets",
    tags=["Hospital — Support Tickets"],
)

app.include_router(
    hospital_announcements.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/announcements",
    tags=["Hospital — Announcements"],
)

app.include_router(
    hospital_documents.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/documents",
    tags=["Hospital — Documents"],
)

app.include_router(
    hospital_communications.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/communications",
    tags=["Hospital — Internal Communications"],
)

app.include_router(
    hospital_notifications.router,
    prefix=f"{settings.API_V1_PREFIX}/hospital/notifications",
    tags=["Hospital — Notifications"],
)

# ── Phase 5A remaining backend milestones ─────────────────────────────────────
# Milestone 21: Rate limiting — applied via decorators, no new router
# Milestone 22: Background jobs — no new router