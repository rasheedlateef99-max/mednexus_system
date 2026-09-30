"""
Hospital Authentication Router
---------------------------------
HTTP endpoints for hospital user authentication via Google OAuth.

Endpoints:
  GET  /hospital/auth/google            → Get Google OAuth URL
  GET  /hospital/auth/google/callback   → Handle Google redirect (main auth entry)
  POST /hospital/auth/refresh           → Refresh hospital access token
  POST /hospital/auth/logout            → Logout current session
  POST /hospital/auth/logout-everywhere → Revoke all sessions for this user
  GET  /hospital/auth/me                → Get current hospital user profile

Rate limiting (M21):
  /google/callback — 10/minute per IP (the credential exchange endpoint)
"""

from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user
from app.schemas.hospital.auth import (
    HospitalAuthTokenResponse,
    RegistrationTokenResponse,
    HospitalUserInfo,
    RefreshTokenRequest,
    LogoutRequest,
    LogoutEverywhereResponse,
)
from app.services.hospital.auth_service import (
    build_google_auth_url,
    handle_google_callback,
    refresh_hospital_token,
    logout,
    logout_everywhere,
)
from app.core.rate_limiter import limiter

router = APIRouter()


@router.get(
    "/google",
    response_model=None,
    summary="Begin Google OAuth Sign-In",
    description=(
        "Returns the Google OAuth authorization URL. "
        "The frontend should redirect the user to this URL "
        "to begin the Google sign-in process."
    ),
)
async def google_auth_url():
    """
    Returns the URL to redirect the user to for Google sign-in.

    The frontend should redirect to the returned auth_url.
    After the user signs in, Google redirects to /google/callback.
    """
    import secrets
    state = secrets.token_urlsafe(32)

    auth_url = build_google_auth_url(state=state)

    return {
        "success": True,
        "data": {
            "auth_url": auth_url,
            "message": "Redirect the user to auth_url to begin Google sign-in",
        }
    }


@router.get(
    "/google/callback",
    summary="Google OAuth Callback",
    description=(
        "Handles the redirect from Google after the user signs in. "
        "Returns either a registration token (new users) or "
        "full access tokens (returning users with active hospitals)."
    ),
)
@limiter.limit("10/minute")
async def google_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Google"),
    state: str | None = Query(None, description="State parameter"),
    error: str | None = Query(None, description="Error from Google if auth was denied"),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Called by Google after the user completes (or cancels) sign-in.
    Rate limited: 10 requests/minute per IP.
    """
    if error:
        return {
            "success": False,
            "error": {
                "code": 400,
                "message": f"Google sign-in was cancelled or denied: {error}",
            }
        }

    client_ip = request.client.host if request.client else "unknown"
    result = await handle_google_callback(db, code, ip_address=client_ip)

    return {
        "success": True,
        "data": result,
    }


@router.post(
    "/refresh",
    response_model=HospitalAuthTokenResponse,
    summary="Refresh Access Token",
    description=(
        "Exchange a valid refresh token for a new access token and refresh token. "
        "The refresh token is validated against the Redis blocklist — "
        "tokens invalidated via logout are rejected."
    ),
)
async def refresh_token(
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Issues new access and refresh tokens if the provided refresh token is valid
    and has not been revoked via logout or logout-everywhere.
    """
    return await refresh_hospital_token(db, body.refresh_token)


@router.post(
    "/logout",
    summary="Logout Current Session",
    description=(
        "Invalidates the provided refresh token by adding it to the Redis blocklist. "
        "The access token expires naturally after its configured window. "
        "Future refresh attempts with this token will be rejected."
    ),
)
async def logout_current_session(
    body: LogoutRequest,
):
    """
    Logs out the current session. The frontend should discard both
    the access token and refresh token after calling this endpoint.
    No authentication required — the refresh token itself is the credential.
    """
    logout(body.refresh_token)
    return {"success": True, "message": "Logged out successfully."}


@router.post(
    "/logout-everywhere",
    response_model=LogoutEverywhereResponse,
    summary="Logout All Sessions",
    description=(
        "Revokes ALL active sessions for the currently authenticated user "
        "by storing a revoke_before timestamp in Redis. "
        "Any refresh token issued before this moment will be rejected on next use."
    ),
)
async def logout_all_sessions(
    ctx: dict = Depends(get_current_hospital_user),
):
    """
    Requires a valid access token (uses the standard hospital auth dependency).
    All refresh tokens issued before this call become invalid immediately.
    """
    revoked_at = await logout_everywhere(str(ctx["user"].id))
    return LogoutEverywhereResponse(
        message="All sessions have been revoked. Please sign in again on all devices.",
        revoked_at=revoked_at,
    )


@router.get(
    "/me",
    response_model=HospitalUserInfo,
    summary="Get Current Hospital User",
    description=(
        "Returns the profile of the currently authenticated hospital user. "
        "Requires a valid hospital access token in the Authorization header."
    ),
)
async def get_me(
    ctx: dict = Depends(get_current_hospital_user),
):
    """
    Returns the authenticated user's profile.
    The user is loaded from the database on every call to reflect
    any changes made since the token was issued (role changes,
    status changes, profile updates).
    """
    return HospitalUserInfo.model_validate(ctx["user"])