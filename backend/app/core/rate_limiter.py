"""
Rate Limiter Configuration
-------------------------------
Configures slowapi for Redis-backed per-IP rate limiting.

Usage in routers:
  from app.core.rate_limiter import limiter
  from slowapi.errors import RateLimitExceeded

  @router.post("/login")
  @limiter.limit("10/minute")
  async def login(request: Request, ...):
      ...

  The `request: Request` parameter MUST be present in the endpoint
  signature for slowapi to extract the client IP. FastAPI will not
  inject it automatically — it must be declared explicitly.

Key extraction:
  Default key is the client's IP address (request.client.host).
  Behind a reverse proxy (Railway/Render in production), the real
  client IP is in the X-Forwarded-For header. slowapi handles this
  automatically when the app is configured with ProxyFix middleware,
  which is added in main.py for production environments.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from app.config import settings


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    default_limits=[],  # No global default — limits applied per-endpoint only
)