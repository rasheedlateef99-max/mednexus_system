"""
MedNexus Custom Exceptions
---------------------------
Defines application-specific exceptions.
FastAPI exception handlers convert these into clean HTTP responses.
"""

from fastapi import HTTPException, status


class MedNexusException(HTTPException):
    """Base exception for all MedNexus errors."""
    pass


# ── Authentication & Authorization ───────────────────────────────────────────

class UnauthorizedException(MedNexusException):
    """Raised when a request has no valid authentication token."""
    def __init__(self, detail: str = "Authentication required"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenException(MedNexusException):
    """Raised when a user is authenticated but lacks permission."""
    def __init__(self, detail: str = "You do not have permission to perform this action"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


# ── Resource Errors ───────────────────────────────────────────────────────────

class NotFoundException(MedNexusException):
    """Raised when a requested resource does not exist."""
    def __init__(self, resource: str = "Resource"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource} not found",
        )


class ConflictException(MedNexusException):
    """Raised when a resource already exists (e.g., duplicate email)."""
    def __init__(self, detail: str = "Resource already exists"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )


# ── Business Logic Errors ─────────────────────────────────────────────────────

class SubscriptionExpiredException(MedNexusException):
    """Raised when a hospital's subscription has expired."""
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Your hospital subscription has expired. Please renew to continue.",
        )


class HospitalSuspendedException(MedNexusException):
    """Raised when a hospital account has been suspended by the platform."""
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This hospital account has been suspended. Contact support.",
        )


class ValidationException(MedNexusException):
    """Raised when business validation fails (beyond basic type checking)."""
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )