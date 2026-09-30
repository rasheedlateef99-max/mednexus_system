"""
MedNexus Configuration Module
------------------------------
Loads all environment variables and exposes them as typed settings.
Pydantic-settings automatically reads from the .env file.
All application configuration should be accessed through this module.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    pydantic-settings automatically reads these from your .env file.
    Variables with defaults are optional — the app starts without them.
    Variables without defaults are required — the app refuses to start
    if they are missing from .env.
    """

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "MedNexus"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development | staging | production

    # ── API ───────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"

    # ── Database ──────────────────────────────────────────────────────────────
    # Tenant database: all hospital operational data
    DATABASE_URL: str

    # Platform database: LATLOMP's own data (hospitals, subscriptions, admins)
    PLATFORM_DATABASE_URL: str

    # ── Redis ─────────────────────────────────────────────────────────────────
    # Used by: rate limiting (slowapi), Celery broker, Celery result backend.
    # All three default to the same Redis instance. Override individually
    # in production if you want to separate concerns (e.g. dedicated Celery
    # Redis vs application Redis).
    REDIS_URL: str = "redis://localhost:6379"

    # ── Celery ────────────────────────────────────────────────────────────────
    # Broker: where Celery sends task messages (tasks go IN here)
    # Result backend: where Celery stores task results (results come OUT here)
    # Both default to REDIS_URL — override only if using separate Redis instances.
    #
    # In production, set these explicitly in your environment:
    #   CELERY_BROKER_URL=redis://:password@your-redis-host:6379/0
    #   CELERY_RESULT_BACKEND=redis://:password@your-redis-host:6379/1
    #
    # Flower monitoring UI (optional, for development visibility):
    #   celery -A app.worker flower --port=5555
    #   Then open http://localhost:5555 to see task history and worker status
    CELERY_BROKER_URL: str = ""       # Falls back to REDIS_URL in worker.py if empty
    CELERY_RESULT_BACKEND: str = ""   # Falls back to REDIS_URL in worker.py if empty

    # ── Security: Hospital JWT ────────────────────────────────────────────────
    # Used to sign tokens for hospital users (doctors, nurses, admins, etc.)
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Security: Platform Admin JWT ──────────────────────────────────────────
    # Completely separate secret for platform admin tokens.
    # Even if a hospital JWT is compromised, platform admin routes remain secure.
    PLATFORM_SECRET_KEY: str

    # ── Google OAuth ──────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""

    # ── Paystack ──────────────────────────────────────────────────────────────
    # Test mode keys are sufficient for structural verification.
    # Switch to live keys (and ideally a separate Paystack business account
    # under MedNexus's own registration) before processing real hospital payments.
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""

    # ── File Storage (Cloudflare R2) ──────────────────────────────────────────
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "mednexus-files"
    R2_PUBLIC_URL: str = ""

    # ── Email ─────────────────────────────────────────────────────────────────
    # Provider: Resend (https://resend.com)
    # Development: use onboarding@resend.dev as FROM_EMAIL (no domain needed)
    # Production:  use noreply@yourdomain.com after domain verification in Resend
    EMAIL_PROVIDER: str = "resend"
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "onboarding@resend.dev"

    # ── Frontend ──────────────────────────────────────────────────────────────
    # Used to build links in emails (invitation links, password reset links, etc.)
    # Development: http://localhost:3000
    # Production:  https://app.mednexus.com (update before going live)
    FRONTEND_URL: str = "http://localhost:3000"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.

    @lru_cache() means this function runs exactly once and caches the result.
    Every subsequent call returns the same object — no repeated file reads.
    """
    return Settings()


# Convenience: import `settings` directly in other modules
settings = get_settings()