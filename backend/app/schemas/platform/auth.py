"""
Platform Admin Authentication Schemas
----------------------------------------
Pydantic models that define the shape of authentication request and
response bodies.

Schemas serve two purposes:
  1. Input validation — FastAPI rejects malformed requests automatically
  2. Response serialization — controls exactly what data is returned to clients

Schemas are NOT database models. They are data transfer objects (DTOs).
"""

from pydantic import BaseModel, EmailStr, Field
from uuid import UUID
from datetime import datetime


# ── Request Schemas (data coming IN from the client) ─────────────────────────

class PlatformLoginRequest(BaseModel):
    """
    Body expected when a platform admin submits their login credentials.
    
    Example request body:
    {
        "email": "admin@mednexus.com",
        "password": "their-secure-password"
    }
    """
    email: EmailStr = Field(
        ...,
        description="Platform admin email address",
        examples=["admin@mednexus.com"],
    )
    password: str = Field(
        ...,
        min_length=8,
        description="Platform admin password",
    )


class TwoFactorVerifyRequest(BaseModel):
    """
    Body expected when a platform admin submits their 2FA code.
    
    This is the second step of login — after email/password succeeds,
    the admin must also submit their current 6-digit authenticator code.
    
    Example request body:
    {
        "temp_token": "eyJhbGci...",
        "code": "123456"
    }
    """
    temp_token: str = Field(
        ...,
        description="Temporary token received after successful password verification",
    )
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",   # Must be exactly 6 digits
        description="6-digit code from authenticator app",
    )


class TwoFactorSetupVerifyRequest(BaseModel):
    """
    Body expected when a platform admin confirms their 2FA setup.
    
    After scanning the QR code, they submit one valid code to confirm
    their authenticator app is working correctly.
    
    Example request body:
    {
        "code": "123456"
    }
    """
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit code from authenticator app to confirm setup",
    )


class RefreshTokenRequest(BaseModel):
    """
    Body expected when requesting a new access token using a refresh token.
    """
    refresh_token: str = Field(
        ...,
        description="Valid refresh token",
    )


# ── Response Schemas (data going OUT to the client) ───────────────────────────

class PlatformAdminInfo(BaseModel):
    """
    Safe representation of a platform admin.
    Only fields that are safe to send to the client are included.
    hashed_password and totp_secret are NEVER included.
    """
    id: UUID
    full_name: str
    email: str
    role: str
    two_factor_enabled: bool
    last_login_at: datetime | None

    model_config = {"from_attributes": True}
    # from_attributes=True allows Pydantic to read from SQLAlchemy model instances


class LoginStepOneResponse(BaseModel):
    """
    Response after successful email/password verification.
    
    If 2FA is enabled:   Returns a temp_token and requires 2FA step
    If 2FA not enabled:  Returns full tokens (new admin first login)
    """
    requires_2fa: bool
    temp_token: str | None = None        # Short-lived token for the 2FA step
    message: str


class AuthTokenResponse(BaseModel):
    """
    Response after complete successful authentication.
    Contains the tokens the frontend stores and uses for all future requests.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    admin: PlatformAdminInfo


class TwoFactorSetupResponse(BaseModel):
    """
    Response when initiating 2FA setup for an admin.
    Contains the QR code for the admin to scan.
    """
    qr_code_base64: str   # PNG image encoded as base64
    secret: str           # Manual entry fallback (some apps prefer this)
    message: str


class MessageResponse(BaseModel):
    """Generic response for operations that only need to confirm success."""
    message: str
    success: bool = True