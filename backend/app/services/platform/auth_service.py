"""
Platform Admin Authentication Service
----------------------------------------
Contains all business logic for platform admin authentication.

Responsibilities:
  - Verify email and password against the database
  - Generate JWT tokens (access + refresh)
  - Manage 2FA setup and verification
  - Handle the two-step login flow

The service layer has no knowledge of HTTP requests or responses.
It receives plain Python objects and returns plain Python objects.
The router translates between HTTP and these plain objects.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError

from app.models.platform.platform_admin import PlatformAdmin
from app.schemas.platform.auth import (
    PlatformLoginRequest,
    LoginStepOneResponse,
    AuthTokenResponse,
    TwoFactorSetupResponse,
    PlatformAdminInfo,
)
from app.core.security import (
    verify_password,
    create_platform_access_token,
    create_platform_refresh_token,
    decode_platform_token,
    generate_totp_secret,
    generate_totp_qr_base64,
    verify_totp_code,
)
from app.core.exceptions import (
    UnauthorizedException,
    ForbiddenException,
)
from app.config import settings


# ── Temporary Token for 2FA Step ─────────────────────────────────────────────
# After password verification succeeds, we issue a short-lived temporary
# token. The admin must present this token alongside their 2FA code in
# the second step. This prevents the 2FA step from being bypassed.

TEMP_TOKEN_EXPIRE_MINUTES = 5  # Admin has 5 minutes to complete 2FA


def _create_temp_token(admin_id: str) -> str:
    """
    Creates a very short-lived token used only between login steps.
    
    This is NOT an access token. It cannot be used to access any resource.
    It only proves that the password step was completed successfully.
    """
    from datetime import datetime, timedelta, timezone
    from jose import jwt

    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=TEMP_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": admin_id,
        "type": "platform_temp",       # Unique type — rejected by all other validators
        "exp": expire,
        "iat": now,
    }

    return jwt.encode(
        payload,
        settings.PLATFORM_SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def _decode_temp_token(token: str) -> str:
    """
    Decodes a temporary token and returns the admin ID.
    Validates that it is specifically a temp token (not an access token).
    
    Returns:
        The admin's UUID as a string
        
    Raises:
        UnauthorizedException if the token is invalid, expired, or wrong type
    """
    try:
        payload = jwt.decode(
            token,
            settings.PLATFORM_SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )

        # Ensure this is a temp token — reject access tokens used here
        if payload.get("type") != "platform_temp":
            raise UnauthorizedException("Invalid token type")

        admin_id = payload.get("sub")
        if not admin_id:
            raise UnauthorizedException("Invalid token payload")

        return admin_id

    except JWTError:
        raise UnauthorizedException("Temporary token is invalid or expired. Please log in again.")


# ── Core Service Functions ────────────────────────────────────────────────────

async def get_admin_by_email(
    db: AsyncSession,
    email: str,
) -> PlatformAdmin | None:
    """
    Fetches a platform admin record by email address.
    
    Args:
        db:    Database session
        email: Email to search for
        
    Returns:
        PlatformAdmin instance or None if not found
    """
    result = await db.execute(
        select(PlatformAdmin).where(PlatformAdmin.email == email.lower())
    )
    return result.scalar_one_or_none()


async def get_admin_by_id(
    db: AsyncSession,
    admin_id: str,
) -> PlatformAdmin | None:
    """
    Fetches a platform admin record by UUID.
    
    Args:
        db:       Database session
        admin_id: UUID string
        
    Returns:
        PlatformAdmin instance or None if not found
    """
    import uuid
    result = await db.execute(
        select(PlatformAdmin).where(PlatformAdmin.id == uuid.UUID(admin_id))
    )
    return result.scalar_one_or_none()


async def login_step_one(
    db: AsyncSession,
    credentials: PlatformLoginRequest,
    ip_address: str = "unknown",
) -> LoginStepOneResponse:
    """
    Step 1 of platform admin login: verify email and password.
    
    Three possible outcomes:
    
    A) Admin not found or password wrong:
       → Raise UnauthorizedException
       → We give the SAME error message for both cases (security best practice:
         never reveal whether an email exists in the system)
    
    B) Admin found, password correct, 2FA enabled:
       → Return a temp_token
       → Admin must complete step 2 (2FA verification)
    
    C) Admin found, password correct, 2FA NOT enabled:
       → This only happens on first login for new admins
       → Return full tokens and prompt them to set up 2FA
    
    Args:
        db:           Platform database session
        credentials:  Email and password from the request body
        ip_address:   Client IP for audit logging
        
    Returns:
        LoginStepOneResponse
    """
    # Look up the admin
    admin = await get_admin_by_email(db, credentials.email)

    # Verify password — same error for "not found" and "wrong password"
    # This prevents attackers from discovering which emails are registered
    if not admin or not verify_password(credentials.password, admin.hashed_password):
        raise UnauthorizedException("Invalid email or password")

    # Check if account is active
    if not admin.is_active:
        raise ForbiddenException("This admin account has been deactivated")

    admin_id_str = str(admin.id)

    # ── Case B: 2FA is set up — require the second step ──────────────────────
    if admin.two_factor_enabled and admin.totp_secret:
        temp_token = _create_temp_token(admin_id_str)
        return LoginStepOneResponse(
            requires_2fa=True,
            temp_token=temp_token,
            message="Password verified. Please enter your 2FA code.",
        )

    # ── Case C: 2FA not yet set up — issue full tokens ───────────────────────
    # This path is only reached by newly created admin accounts
    # We will prompt them to set up 2FA from the dashboard
    access_token = create_platform_access_token(
        subject=admin_id_str,
        additional_claims={"role": admin.role.value},
    )
    refresh_token = create_platform_refresh_token(subject=admin_id_str)

    # Update last login tracking
    admin.last_login_at = datetime.now(timezone.utc)
    admin.last_login_ip = ip_address
    await db.commit()
    await db.refresh(admin)

    return LoginStepOneResponse(
        requires_2fa=False,
        temp_token=None,
        message="Login successful. Please set up two-factor authentication.",
    )


async def login_step_two(
    db: AsyncSession,
    temp_token: str,
    code: str,
    ip_address: str = "unknown",
) -> AuthTokenResponse:
    """
    Step 2 of platform admin login: verify the 2FA code.
    
    The admin presents:
      - The temp_token received from step one
      - The current 6-digit code from their authenticator app
    
    If both are valid, we issue full access and refresh tokens.
    
    Args:
        db:         Platform database session
        temp_token: Short-lived token from step one
        code:       6-digit TOTP code from authenticator app
        ip_address: Client IP for audit logging
        
    Returns:
        AuthTokenResponse with full access and refresh tokens
    """
    # Decode and validate the temp token
    admin_id_str = _decode_temp_token(temp_token)

    # Load the admin from the database
    admin = await get_admin_by_id(db, admin_id_str)
    if not admin or not admin.is_active:
        raise UnauthorizedException("Admin account not found or inactive")

    # Verify the TOTP code
    if not admin.totp_secret or not verify_totp_code(admin.totp_secret, code):
        raise UnauthorizedException("Invalid or expired 2FA code")

    # Issue full tokens
    access_token = create_platform_access_token(
        subject=admin_id_str,
        additional_claims={"role": admin.role.value},
    )
    refresh_token = create_platform_refresh_token(subject=admin_id_str)

    # Update last login tracking
    admin.last_login_at = datetime.now(timezone.utc)
    admin.last_login_ip = ip_address
    await db.commit()
    await db.refresh(admin)

    return AuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        admin=PlatformAdminInfo.model_validate(admin),
    )


async def setup_two_factor(
    db: AsyncSession,
    admin: PlatformAdmin,
) -> TwoFactorSetupResponse:
    """
    Initiates 2FA setup for a platform admin.
    
    Generates a new TOTP secret, stores it (unconfirmed) in the database,
    and returns a QR code for the admin to scan.
    
    The secret is stored but two_factor_enabled remains False until the
    admin confirms the setup by submitting a valid code.
    
    Args:
        db:    Platform database session
        admin: The authenticated platform admin
        
    Returns:
        TwoFactorSetupResponse with QR code and manual entry secret
    """
    # Generate a new TOTP secret
    secret = generate_totp_secret()

    # Store it — but do NOT yet mark 2FA as enabled
    admin.totp_secret = secret
    await db.commit()

    # Generate QR code
    qr_base64 = generate_totp_qr_base64(secret, admin.email)

    return TwoFactorSetupResponse(
        qr_code_base64=qr_base64,
        secret=secret,
        message=(
            "Scan the QR code with your authenticator app (Google Authenticator, "
            "Authy, etc.), then submit a valid code to confirm setup."
        ),
    )


async def confirm_two_factor_setup(
    db: AsyncSession,
    admin: PlatformAdmin,
    code: str,
) -> dict:
    """
    Confirms 2FA setup by verifying the first code from the admin's authenticator.
    
    Only after this confirmation is two_factor_enabled set to True.
    This ensures we never lock an admin out with a broken 2FA setup.
    
    Args:
        db:    Platform database session
        admin: The authenticated platform admin
        code:  6-digit code from their authenticator app
        
    Returns:
        Success message dict
    """
    if not admin.totp_secret:
        raise ForbiddenException("2FA setup has not been initiated. Call the setup endpoint first.")

    if not verify_totp_code(admin.totp_secret, code):
        raise UnauthorizedException("Invalid 2FA code. Please try again.")

    # Confirmed — activate 2FA
    admin.two_factor_enabled = True
    await db.commit()

    return {"message": "Two-factor authentication has been successfully enabled."}


async def refresh_access_token(
    db: AsyncSession,
    refresh_token: str,
) -> AuthTokenResponse:
    """
    Issues a new access token using a valid refresh token.
    
    Called when the frontend detects an expired access token.
    The user does not need to log in again — this happens silently.
    
    Args:
        db:            Platform database session
        refresh_token: The refresh token stored by the frontend
        
    Returns:
        New AuthTokenResponse with fresh access and refresh tokens
    """
    try:
        payload = decode_platform_token(refresh_token)

        # Ensure it is specifically a refresh token
        if payload.get("type") != "platform_refresh":
            raise UnauthorizedException("Invalid token type")

        admin_id_str = payload.get("sub")
        if not admin_id_str:
            raise UnauthorizedException("Invalid token payload")

    except JWTError:
        raise UnauthorizedException("Refresh token is invalid or expired. Please log in again.")

    admin = await get_admin_by_id(db, admin_id_str)
    if not admin or not admin.is_active:
        raise UnauthorizedException("Admin account not found or inactive")

    # Issue fresh tokens
    new_access_token = create_platform_access_token(
        subject=admin_id_str,
        additional_claims={"role": admin.role.value},
    )
    new_refresh_token = create_platform_refresh_token(subject=admin_id_str)

    return AuthTokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        admin=PlatformAdminInfo.model_validate(admin),
    )