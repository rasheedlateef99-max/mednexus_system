"""
Hospital Authentication Service
----------------------------------
Business logic for hospital user authentication via Google OAuth.

The authentication flow has three possible outcomes depending on the
user's state in the system:

Outcome A — Brand new user (never signed in before):
  → Create HospitalUser record (no hospital_id yet)
  → Issue REGISTRATION token
  → Frontend sends them to the registration flow

Outcome B — Returning user with a PENDING or REJECTED hospital:
  → Issue REGISTRATION token
  → Frontend shows them their application status

Outcome C — Returning user with an ACTIVE hospital:
  → Issue full HOSPITAL ACCESS token
  → Frontend sends them to their hospital dashboard

This state machine is the core of the onboarding experience.

Refresh Token Security (Phase 5B):
  Refresh tokens are stateless JWTs but can be revoked via Redis:

  Single-session logout:
    The token's JTI (jti claim — a unique UUID per token) is stored
    in Redis with a TTL matching the token's remaining lifetime.
    Any subsequent refresh attempt with that JTI is rejected.

  Logout-everywhere:
    A revoke_before:{user_id} key is stored in Redis with the current
    UTC timestamp. Any refresh token with iat (issued-at) before this
    timestamp is rejected, regardless of JTI. This invalidates ALL
    active sessions for the user in one write.
"""

import uuid
import httpx
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError

from app.config import settings
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole, HospitalUserStatus
from app.core.exceptions import UnauthorizedException, ForbiddenException
from app.schemas.hospital.auth import (
    HospitalUserInfo,
    RegistrationTokenResponse,
    HospitalAuthTokenResponse,
)


# ── Google OAuth Constants ────────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

GOOGLE_SCOPES = "openid email profile"


# ── Token Configuration ───────────────────────────────────────────────────────

REGISTRATION_TOKEN_EXPIRE_MINUTES = 60
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30


# ── Redis Helper ──────────────────────────────────────────────────────────────

def _get_redis_client():
    """
    Returns a synchronous Redis client for blocklist operations.
    Uses the same REDIS_URL as the rest of the application.
    Synchronous because this service is called from async FastAPI
    endpoints via run_in_executor or directly (redis-py 4+ supports
    both sync and async clients — we use sync here for simplicity
    since blocklist operations are fast key lookups).
    """
    import redis as redis_lib
    return redis_lib.from_url(settings.REDIS_URL, decode_responses=True)


# ── Google OAuth URL Builder ──────────────────────────────────────────────────

def build_google_auth_url(state: str | None = None) -> str:
    """
    Builds the Google OAuth authorization URL.

    The frontend redirects the user to this URL.
    Google shows the "Sign in with Google" consent screen.
    After the user approves, Google redirects to our callback URL.

    Args:
        state: Optional random string for CSRF protection.

    Returns:
        Full Google OAuth URL with all required parameters
    """
    import urllib.parse

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "select_account",
    }

    if state:
        params["state"] = state

    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


# ── Google OAuth Exchange ─────────────────────────────────────────────────────

async def exchange_google_code(code: str) -> dict[str, Any]:
    """
    Exchanges a Google authorization code for user profile data.

    Args:
        code: The authorization code received from Google's callback

    Returns:
        Dictionary with: id, email, name, picture, verified_email

    Raises:
        UnauthorizedException if Google rejects the code
    """
    async with httpx.AsyncClient() as client:

        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )

        if token_response.status_code != 200:
            raise UnauthorizedException(
                "Failed to exchange Google authorization code. "
                "The code may have expired — please try signing in again."
            )

        token_data = token_response.json()
        google_access_token = token_data.get("access_token")

        if not google_access_token:
            raise UnauthorizedException("Google did not return an access token")

        profile_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )

        if profile_response.status_code != 200:
            raise UnauthorizedException(
                "Failed to fetch Google profile information"
            )

        profile = profile_response.json()

        if not profile.get("verified_email"):
            raise UnauthorizedException(
                "Your Google account email is not verified. "
                "Please verify your email with Google before signing in."
            )

        return profile


# ── Token Creation ────────────────────────────────────────────────────────────

def create_registration_token(user_id: str) -> str:
    """
    Creates a short-lived token for users who have authenticated with
    Google but have not yet completed hospital registration.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=REGISTRATION_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": user_id,
        "type": "hospital_registration",
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def create_hospital_access_token(
    user_id: str,
    hospital_id: str,
    role: str,
) -> str:
    """
    Creates a full access token for a hospital user with an active workspace.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": user_id,
        "type": "hospital_access",
        "hospital_id": hospital_id,
        "role": role,
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def create_hospital_refresh_token(user_id: str, hospital_id: str) -> str:
    """
    Creates a longer-lived refresh token for silent token renewal.

    Embeds a jti (JWT ID) — a unique UUID per token — so individual
    tokens can be added to the Redis blocklist during logout without
    invalidating all other active sessions for the same user.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    payload = {
        "sub": user_id,
        "type": "hospital_refresh",
        "hospital_id": hospital_id,
        "jti": str(uuid.uuid4()),   # Unique token ID for blocklist targeting
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


# ── Core Auth Flow ────────────────────────────────────────────────────────────

async def handle_google_callback(
    db: AsyncSession,
    code: str,
    ip_address: str = "unknown",
) -> RegistrationTokenResponse | HospitalAuthTokenResponse:
    """
    The heart of hospital authentication.

    Called after Google redirects back to our callback URL.
    Handles all three outcomes described in the module docstring.
    """

    google_profile = await exchange_google_code(code)

    google_id = google_profile["id"]
    email = google_profile["email"].lower()
    full_name = google_profile.get("name", email)
    avatar_url = google_profile.get("picture")

    result = await db.execute(
        select(HospitalUser).where(HospitalUser.google_id == google_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        result = await db.execute(
            select(HospitalUser).where(HospitalUser.email == email)
        )
        user = result.scalar_one_or_none()

    if not user:
        user = HospitalUser(
            hospital_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            full_name=full_name,
            email=email,
            google_id=google_id,
            avatar_url=avatar_url,
            role=HospitalUserRole.HOSPITAL_ADMIN,
            status=HospitalUserStatus.ACTIVE,
            is_owner=True,
        )
        db.add(user)
        await db.flush()
    else:
        user.google_id = google_id
        user.avatar_url = avatar_url
        user.full_name = full_name
        user.last_login_at = datetime.now(timezone.utc)
        user.last_login_ip = ip_address

    await db.commit()
    await db.refresh(user)

    user_id_str = str(user.id)
    sentinel_id = "00000000-0000-0000-0000-000000000000"
    has_hospital = (
        user.hospital_id is not None
        and str(user.hospital_id) != sentinel_id
    )

    if not has_hospital:
        reg_token = create_registration_token(user_id_str)
        return RegistrationTokenResponse(
            registration_token=reg_token,
            user=HospitalUserInfo.model_validate(user),
            message="Google sign-in successful. Please complete hospital registration.",
        )

    from app.services.platform.hospital_service import get_hospital_status
    hospital_status = await get_hospital_status(str(user.hospital_id))

    if hospital_status != "active":
        reg_token = create_registration_token(user_id_str)
        status_messages = {
            "pending_review": (
                "Your hospital application is under review. "
                "We will notify you once approved."
            ),
            "suspended": (
                "Your hospital account has been suspended. "
                "Please contact support."
            ),
            "deactivated": (
                "Your hospital account has been deactivated. "
                "Please contact support."
            ),
        }
        return RegistrationTokenResponse(
            registration_token=reg_token,
            user=HospitalUserInfo.model_validate(user),
            message=status_messages.get(
                hospital_status,
                "Your hospital application is being processed."
            ),
        )

    hospital_id_str = str(user.hospital_id)
    access_token = create_hospital_access_token(
        user_id=user_id_str,
        hospital_id=hospital_id_str,
        role=user.role.value,
    )
    refresh_token = create_hospital_refresh_token(
        user_id=user_id_str,
        hospital_id=hospital_id_str,
    )

    from app.services.platform.hospital_service import get_hospital_name
    hospital_name = await get_hospital_name(hospital_id_str)

    return HospitalAuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=HospitalUserInfo.model_validate(user),
        hospital_id=user.hospital_id,
        hospital_name=hospital_name,
    )


# ── Refresh Token Operations (Phase 5B) ──────────────────────────────────────

async def refresh_hospital_token(
    db: AsyncSession,
    refresh_token: str,
) -> HospitalAuthTokenResponse:
    """
    Validates a refresh token and issues a new access token.

    Checks, in order:
      1. Token signature and expiry (JWT verification)
      2. Token type is 'hospital_refresh'
      3. JTI not on the Redis blocklist (single-session logout)
      4. Token iat not before the user's revoke_before timestamp (logout-everywhere)
      5. User still exists and belongs to an active hospital

    Returns a new HospitalAuthTokenResponse with fresh access and refresh tokens.
    The old refresh token is NOT immediately revoked here — the client should
    discard it and use the new one. Call logout() explicitly to blocklist a token.
    """
    try:
        payload = jwt.decode(
            refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        raise UnauthorizedException("Invalid or expired refresh token.")

    if payload.get("type") != "hospital_refresh":
        raise UnauthorizedException("Invalid token type.")

    user_id = payload.get("sub")
    hospital_id = payload.get("hospital_id")
    jti = payload.get("jti")
    iat = payload.get("iat")

    if not user_id or not hospital_id:
        raise UnauthorizedException("Malformed refresh token.")

    # Check JTI blocklist (single-session logout)
    if jti:
        r = _get_redis_client()
        if r.exists(f"blocklist:jti:{jti}"):
            raise UnauthorizedException("This session has been logged out.")

    # Check revoke_before (logout-everywhere)
    if iat:
        r = _get_redis_client()
        revoke_before = r.get(f"revoke_before:{user_id}")
        if revoke_before and float(iat) < float(revoke_before):
            raise UnauthorizedException(
                "All sessions for this account have been revoked. Please sign in again."
            )

    # Confirm user still exists and is active
    result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == uuid.UUID(user_id),
        )
    )
    user = result.scalar_one_or_none()
    if not user or user.status != HospitalUserStatus.ACTIVE:
        raise UnauthorizedException("User account is inactive or not found.")

    # Issue new tokens
    new_access_token = create_hospital_access_token(
        user_id=user_id,
        hospital_id=hospital_id,
        role=user.role.value,
    )
    new_refresh_token = create_hospital_refresh_token(
        user_id=user_id,
        hospital_id=hospital_id,
    )

    from app.services.platform.hospital_service import get_hospital_name
    hospital_name = await get_hospital_name(hospital_id)

    return HospitalAuthTokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        user=HospitalUserInfo.model_validate(user),
        hospital_id=uuid.UUID(hospital_id),
        hospital_name=hospital_name,
    )


def logout(refresh_token: str) -> None:
    """
    Invalidates a single refresh token by adding its JTI to the Redis blocklist.

    The JTI key is stored with a TTL equal to the token's remaining lifetime
    so the Redis key expires automatically when the token would have anyway —
    no manual cleanup needed.

    If the token has no JTI (tokens issued before Phase 5B) or is already
    expired, this is a no-op — the token can't be used regardless.
    """
    try:
        payload = jwt.decode(
            refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        return  # Already invalid — nothing to do

    jti = payload.get("jti")
    exp = payload.get("exp")

    if not jti or not exp:
        return  # Pre-Phase-5B token without JTI — already expired or harmless

    now_ts = datetime.now(timezone.utc).timestamp()
    ttl_seconds = int(exp - now_ts)

    if ttl_seconds <= 0:
        return  # Already expired

    r = _get_redis_client()
    r.setex(f"blocklist:jti:{jti}", ttl_seconds, "1")


async def logout_everywhere(user_id: str) -> datetime:
    """
    Revokes ALL active sessions for a user by storing a revoke_before
    timestamp in Redis. Any refresh token with iat before this timestamp
    is rejected on the next refresh attempt.

    The Redis key is stored with a 30-day TTL (matching the maximum
    refresh token lifetime) so it expires automatically.

    Returns the revocation timestamp for the response body.
    """
    now = datetime.now(timezone.utc)
    r = _get_redis_client()
    r.setex(
        f"revoke_before:{user_id}",
        REFRESH_TOKEN_EXPIRE_DAYS * 86400,  # 30 days in seconds
        str(now.timestamp()),
    )
    return now