"""
Hospital Authentication Schemas
----------------------------------
Pydantic models for hospital user authentication request and response bodies.

These are completely separate from platform admin auth schemas.
Hospital users authenticate with Google OAuth — no email/password form.
"""

from pydantic import BaseModel, EmailStr, Field
from uuid import UUID
from datetime import datetime


# ── OAuth Flow Schemas ────────────────────────────────────────────────────────

class GoogleAuthURLResponse(BaseModel):
    """
    Response containing the Google OAuth authorization URL.

    The frontend redirects the user to this URL to begin Google sign-in.
    """
    auth_url: str
    message: str = "Redirect the user to auth_url to begin Google sign-in"


class GoogleCallbackRequest(BaseModel):
    """
    Data received from Google after the user authorizes the application.

    Google redirects to our callback URL with these query parameters.
    We extract them and exchange the code for user profile data.
    """
    code: str = Field(..., description="Authorization code from Google")
    state: str | None = Field(None, description="State parameter for CSRF protection")


# ── Token & Session Schemas ───────────────────────────────────────────────────

class HospitalUserInfo(BaseModel):
    """
    Safe representation of a hospital user.
    Never includes hashed_password or sensitive internal fields.
    """
    id: UUID
    hospital_id: UUID | None       # None if registration is not yet complete
    full_name: str
    email: str
    role: str
    status: str
    avatar_url: str | None
    is_owner: bool
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class RegistrationTokenResponse(BaseModel):
    """
    Token issued after Google sign-in when the user has no hospital yet.

    This is NOT a full access token. It only permits access to the
    registration endpoints. The user cannot access any hospital
    workspace with this token.
    """
    registration_token: str
    token_type: str = "bearer"
    user: HospitalUserInfo
    message: str = "Google sign-in successful. Please complete hospital registration."


class HospitalAuthTokenResponse(BaseModel):
    """
    Full authentication tokens issued after successful login to an
    approved and active hospital workspace.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: HospitalUserInfo
    hospital_id: UUID
    hospital_name: str


class RefreshTokenRequest(BaseModel):
    """Body for refreshing an expired hospital access token."""
    refresh_token: str


class LogoutRequest(BaseModel):
    """
    Body for logging out a single session.
    The refresh token being invalidated must be supplied so it can be
    added to the Redis blocklist for the remainder of its natural lifetime.
    """
    refresh_token: str


class LogoutEverywhereResponse(BaseModel):
    """Response confirming all sessions for this user have been revoked."""
    message: str
    revoked_at: datetime