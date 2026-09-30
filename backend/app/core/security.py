"""
MedNexus Security Utilities
------------------------------
Core cryptographic functions used throughout the application.

This module handles:
  - Password hashing and verification (bcrypt via passlib)
  - JWT access token creation and decoding
  - Separate token handling for platform admins vs hospital users
  - TOTP (2FA) secret generation and code verification

Nothing in this module knows about HTTP requests or database sessions.
It is pure security logic, callable from anywhere.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext
import pyotp
import qrcode
import qrcode.image.svg
from io import BytesIO
import base64

from app.config import settings


# ── Password Hashing ──────────────────────────────────────────────────────────

# CryptContext manages password hashing schemes.
# We use bcrypt — the industry standard for password storage.
# "deprecated='auto'" means older schemes are automatically marked
# for rehashing when users next log in (future-proofing).
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """
    Hashes a plain-text password using bcrypt.
    
    The result is a string like:
      $2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36zLSRuOTfHxIbMDXicg3SK
    
    This hash is what gets stored in the database.
    The plain password is never stored anywhere.
    
    Args:
        plain_password: The password the user typed
        
    Returns:
        A bcrypt hash string safe to store in the database
    """
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain-text password against a stored bcrypt hash.
    
    bcrypt is designed so that even if two users have the same password,
    their hashes will be different (due to random salting).
    This function handles that comparison correctly.
    
    Args:
        plain_password:   What the user typed during login
        hashed_password:  What is stored in the database
        
    Returns:
        True if the password is correct, False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT Token Creation ────────────────────────────────────────────────────────

def create_platform_access_token(
    subject: str,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """
    Creates a JWT access token for a platform admin.
    
    Uses PLATFORM_SECRET_KEY — completely separate from hospital tokens.
    Short expiry (15 minutes by default) forces frequent re-validation.
    
    What is a JWT?
    A JWT (JSON Web Token) is a signed string containing claims (data).
    Example decoded payload:
      {
        "sub": "admin-uuid-here",      # who this token belongs to
        "type": "platform_access",     # what kind of token this is
        "role": "super_admin",         # their role
        "exp": 1234567890,             # when it expires (Unix timestamp)
        "iat": 1234567000,             # when it was issued
      }
    
    The token is signed with PLATFORM_SECRET_KEY. Any tampering with the
    payload invalidates the signature, and verification will reject it.
    
    Args:
        subject:           The platform admin's UUID (as string)
        additional_claims: Extra data to embed in the token (role, etc.)
        
    Returns:
        A signed JWT string
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict[str, Any] = {
        "sub": subject,                # Subject: who owns this token
        "type": "platform_access",     # Token type: guards against token reuse
        "iat": now,                    # Issued at
        "exp": expire,                 # Expiry
    }

    if additional_claims:
        payload.update(additional_claims)

    return jwt.encode(
        payload,
        settings.PLATFORM_SECRET_KEY,  # Platform-specific secret
        algorithm=settings.ALGORITHM,
    )


def create_platform_refresh_token(subject: str) -> str:
    """
    Creates a longer-lived refresh token for platform admins.
    
    When the access token expires (after 15 minutes), the frontend
    uses the refresh token to silently get a new access token without
    forcing the admin to log in again.
    
    Refresh tokens live longer (7 days) but contain less information.
    They are only accepted by the /refresh endpoint, not general API routes.
    
    Args:
        subject: The platform admin's UUID (as string)
        
    Returns:
        A signed JWT refresh token string
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    payload: dict[str, Any] = {
        "sub": subject,
        "type": "platform_refresh",    # Different type — cannot be used as access token
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.PLATFORM_SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_platform_token(token: str) -> dict[str, Any]:
    """
    Decodes and validates a platform admin JWT token.
    
    Raises JWTError if:
      - The token signature is invalid (tampered)
      - The token has expired
      - The token format is malformed
    
    Args:
        token: The JWT string from the Authorization header
        
    Returns:
        The decoded payload dictionary
        
    Raises:
        JWTError: If the token is invalid for any reason
    """
    return jwt.decode(
        token,
        settings.PLATFORM_SECRET_KEY,  # Must use the same key that signed it
        algorithms=[settings.ALGORITHM],
    )


# ── Two-Factor Authentication (TOTP) ─────────────────────────────────────────

def generate_totp_secret() -> str:
    """
    Generates a new random TOTP secret for a platform admin.
    
    This secret is stored (encrypted) in the database and used to
    generate and verify 6-digit codes via Google Authenticator.
    
    Returns:
        A base32-encoded random secret string
    """
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    """
    Creates the otpauth:// URI that encodes the TOTP secret.
    
    This URI is what gets encoded into the QR code.
    When scanned by Google Authenticator, it adds the account automatically.
    
    Args:
        secret: The TOTP secret from generate_totp_secret()
        email:  The admin's email (used as the account label in the app)
        
    Returns:
        An otpauth:// URI string
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(
        name=email,
        issuer_name=settings.APP_NAME,
    )


def generate_totp_qr_base64(secret: str, email: str) -> str:
    """
    Generates a QR code image for the TOTP secret and returns it as
    a base64-encoded PNG string.
    
    The frontend can display this directly in an <img> tag:
      <img src="data:image/png;base64,{returned_string}" />
    
    Args:
        secret: The TOTP secret
        email:  The admin's email
        
    Returns:
        Base64-encoded PNG image of the QR code
    """
    uri = get_totp_uri(secret, email)

    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def verify_totp_code(secret: str, code: str) -> bool:
    """
    Verifies a 6-digit TOTP code against the stored secret.
    
    TOTP codes are time-based — they change every 30 seconds.
    The valid_window=1 parameter allows for one 30-second window of
    clock drift in either direction, which handles minor time differences
    between the admin's phone and the server.
    
    Args:
        secret: The stored TOTP secret for this admin
        code:   The 6-digit code the admin typed
        
    Returns:
        True if the code is valid, False otherwise
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)