"""
Platform Admin Authentication Router
---------------------------------------
HTTP endpoints for platform admin authentication.

Endpoints:
  POST /platform/auth/login          → Step 1: Email + password
  POST /platform/auth/verify-2fa     → Step 2: 2FA code verification
  POST /platform/auth/refresh        → Refresh access token
  POST /platform/auth/2fa/setup      → Initiate 2FA setup (protected)
  POST /platform/auth/2fa/confirm    → Confirm 2FA setup (protected)
  GET  /platform/auth/me             → Get current admin profile (protected)

Rate limiting (M21):
  /login      — 5/minute per IP
  /verify-2fa — 5/minute per IP
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_platform_db
from app.schemas.platform.auth import (
    PlatformLoginRequest,
    TwoFactorVerifyRequest,
    TwoFactorSetupVerifyRequest,
    RefreshTokenRequest,
    LoginStepOneResponse,
    AuthTokenResponse,
    TwoFactorSetupResponse,
    PlatformAdminInfo,
    MessageResponse,
)
from app.services.platform.auth_service import (
    login_step_one,
    login_step_two,
    setup_two_factor,
    confirm_two_factor_setup,
    refresh_access_token,
)
from app.core.dependencies import get_current_platform_admin
from app.models.platform.platform_admin import PlatformAdmin
from app.core.rate_limiter import limiter

router = APIRouter()


@router.post(
    "/login",
    response_model=LoginStepOneResponse,
    summary="Platform Admin Login — Step 1",
    description=(
        "Verifies email and password. "
        "If 2FA is enabled, returns a temp_token for the 2FA step. "
        "If 2FA is not yet set up (new admin), returns full tokens."
    ),
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    credentials: PlatformLoginRequest,
    db: AsyncSession = Depends(get_platform_db),
):
    """
    Step 1 of platform admin authentication.
    Rate limited: 5 requests/minute per IP.
    """
    client_ip = request.client.host if request.client else "unknown"
    return await login_step_one(db, credentials, ip_address=client_ip)


@router.post(
    "/verify-2fa",
    response_model=AuthTokenResponse,
    summary="Platform Admin Login — Step 2 (2FA Verification)",
    description=(
        "Submit the temp_token from Step 1 along with the 6-digit code "
        "from your authenticator app. Returns full access and refresh tokens."
    ),
)
@limiter.limit("5/minute")
async def verify_two_factor(
    request: Request,
    body: TwoFactorVerifyRequest,
    db: AsyncSession = Depends(get_platform_db),
):
    """
    Step 2 of platform admin authentication.
    Rate limited: 5 requests/minute per IP.
    """
    client_ip = request.client.host if request.client else "unknown"
    return await login_step_two(
        db,
        temp_token=body.temp_token,
        code=body.code,
        ip_address=client_ip,
    )


@router.post(
    "/refresh",
    response_model=AuthTokenResponse,
    summary="Refresh Access Token",
    description="Exchange a valid refresh token for a new access token.",
)
async def refresh_token(
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_platform_db),
):
    return await refresh_access_token(db, body.refresh_token)


@router.get(
    "/me",
    response_model=PlatformAdminInfo,
    summary="Get Current Platform Admin",
    description="Returns the profile of the currently authenticated platform admin.",
)
async def get_me(
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    return PlatformAdminInfo.model_validate(admin)


@router.post(
    "/2fa/setup",
    response_model=TwoFactorSetupResponse,
    summary="Initiate 2FA Setup",
    description=(
        "Generates a new TOTP secret and QR code for the authenticated admin. "
        "Scan the QR code with your authenticator app, then confirm with /2fa/confirm."
    ),
)
async def initiate_2fa_setup(
    admin: PlatformAdmin = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_platform_db),
):
    return await setup_two_factor(db, admin)


@router.post(
    "/2fa/confirm",
    response_model=MessageResponse,
    summary="Confirm 2FA Setup",
    description=(
        "Submit a valid 6-digit code from your authenticator app to confirm "
        "2FA setup is working. After this, all future logins require 2FA."
    ),
)
async def confirm_2fa(
    body: TwoFactorSetupVerifyRequest,
    admin: PlatformAdmin = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_platform_db),
):
    result = await confirm_two_factor_setup(db, admin, body.code)
    return MessageResponse(message=result["message"])