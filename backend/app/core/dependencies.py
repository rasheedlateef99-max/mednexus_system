"""
MedNexus FastAPI Dependencies
--------------------------------
Dependency injection functions for route protection.

FastAPI's dependency injection system works like this:
  1. You declare a dependency in a route: Depends(get_current_platform_admin)
  2. Before your route runs, FastAPI calls that function
  3. If the function raises an exception, the route never runs
  4. If it succeeds, the return value is passed into your route

This is the cleanest way to protect routes in FastAPI.
No middleware, no manual token checking in every route.

Two separate authentication systems live here:
  1. Platform admin auth  — uses PLATFORM_SECRET_KEY, guards /platform/* routes
  2. Hospital user auth   — uses SECRET_KEY, guards /hospital/* routes

These two systems are cryptographically independent. A token from one
system is always rejected by the other.
"""

from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError
import uuid

from app.database import get_platform_db, get_tenant_db
from app.core.security import decode_platform_token
from app.core.exceptions import UnauthorizedException, ForbiddenException
from app.models.platform.platform_admin import PlatformAdmin, PlatformAdminRole


# ── Token Extractors ──────────────────────────────────────────────────────────
# HTTPBearer reads the Authorization: Bearer <token> header.
# auto_error=False means we handle the missing token ourselves
# so we can return our custom JSON error format instead of FastAPI's default.

# Used for platform admin routes
platform_bearer_scheme = HTTPBearer(auto_error=False)

# Used for hospital user routes
# Separate scheme instance so Swagger UI shows them independently
hospital_bearer_scheme = HTTPBearer(auto_error=False)


# ════════════════════════════════════════════════════════════════════════════════
# PLATFORM ADMIN AUTHENTICATION
# Guards all /api/v1/platform/* routes
# Uses PLATFORM_SECRET_KEY — completely separate from hospital tokens
# ════════════════════════════════════════════════════════════════════════════════

async def get_current_platform_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(platform_bearer_scheme),
    db: AsyncSession = Depends(get_platform_db),
) -> PlatformAdmin:
    """
    Dependency that validates a platform admin JWT and returns the admin.

    Usage in a route:
        @router.get("/some-protected-route")
        async def my_route(
            admin: PlatformAdmin = Depends(get_current_platform_admin)
        ):
            return {"message": f"Hello {admin.full_name}"}

    What this does:
      1. Reads the Authorization: Bearer <token> header
      2. Decodes and validates the JWT using PLATFORM_SECRET_KEY
      3. Confirms the token type is "platform_access" (not refresh, not temp)
      4. Loads the admin from the database
      5. Confirms the admin account is still active
      6. Returns the PlatformAdmin instance to the route

    Raises UnauthorizedException if anything fails.
    """
    # Step 1: Check that a token was provided
    if not credentials:
        raise UnauthorizedException("No authentication token provided")

    token = credentials.credentials

    # Step 2: Decode and validate the token
    try:
        payload = decode_platform_token(token)
    except JWTError:
        raise UnauthorizedException("Token is invalid or expired")

    # Step 3: Confirm token type
    if payload.get("type") != "platform_access":
        raise UnauthorizedException("Invalid token type")

    # Step 4: Extract the admin ID
    admin_id_str = payload.get("sub")
    if not admin_id_str:
        raise UnauthorizedException("Token payload is missing subject")

    # Step 5: Load admin from database
    try:
        result = await db.execute(
            select(PlatformAdmin).where(
                PlatformAdmin.id == uuid.UUID(admin_id_str)
            )
        )
        admin = result.scalar_one_or_none()
    except Exception:
        raise UnauthorizedException("Could not validate credentials")

    if not admin:
        raise UnauthorizedException("Admin account not found")

    # Step 6: Confirm account is still active
    if not admin.is_active:
        raise ForbiddenException("This admin account has been deactivated")

    return admin


async def require_super_admin(
    admin: PlatformAdmin = Depends(get_current_platform_admin),
) -> PlatformAdmin:
    """
    Dependency that requires the admin to have the super_admin role.

    Use for routes that manage other admin accounts or critical platform settings.

    Usage:
        @router.delete("/platform-admins/{id}")
        async def delete_admin(
            admin: PlatformAdmin = Depends(require_super_admin)
        ):
            ...
    """
    if admin.role != PlatformAdminRole.SUPER_ADMIN:
        raise ForbiddenException("This action requires super admin privileges")
    return admin


async def require_admin_or_above(
    admin: PlatformAdmin = Depends(get_current_platform_admin),
) -> PlatformAdmin:
    """
    Dependency that requires admin or super_admin role.
    Excludes support-only accounts from write operations.
    """
    if admin.role == PlatformAdminRole.SUPPORT:
        raise ForbiddenException("This action requires admin privileges")
    return admin


# ════════════════════════════════════════════════════════════════════════════════
# HOSPITAL USER AUTHENTICATION
# Guards all /api/v1/hospital/* routes
# Uses SECRET_KEY — completely separate from platform admin tokens
# ════════════════════════════════════════════════════════════════════════════════

async def get_current_hospital_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(hospital_bearer_scheme),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Dependency that validates a hospital user JWT and returns the user.

    This is the primary auth guard for all hospital workspace routes.
    It validates the token, extracts the hospital_id, and makes both
    the user and hospital_id available to every protected route.

    Usage in a route:
        @router.get("/patients")
        async def list_patients(
            current_user = Depends(get_current_hospital_user)
        ):
            hospital_id = current_user["hospital_id"]
            user = current_user["user"]

    What this does:
      1. Reads the Authorization: Bearer <token> header
      2. Decodes and validates the JWT using SECRET_KEY
      3. Confirms the token type is "hospital_access"
      4. Extracts hospital_id and role directly from the token
         (avoids a DB lookup on every request for performance)
      5. Loads the user from the tenant database to confirm they still exist
         and are still active
      6. Returns a context dict with the user and hospital_id

    Why we return a dict instead of just the user:
      The hospital_id is embedded in the token and is needed on virtually
      every route handler to scope database queries. Returning it alongside
      the user object saves each route from having to extract it separately.

    Raises UnauthorizedException if anything fails.
    """
    from app.config import settings
    from app.models.tenant.hospital_user import HospitalUser, HospitalUserStatus
    from jose import jwt as jose_jwt

    # Step 1: Check token was provided
    if not credentials:
        raise UnauthorizedException("No authentication token provided")

    token = credentials.credentials

    # Step 2: Decode and validate using SECRET_KEY (not PLATFORM_SECRET_KEY)
    try:
        payload = jose_jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        raise UnauthorizedException("Token is invalid or expired")

    # Step 3: Confirm this is a hospital access token
    # This check prevents refresh tokens and registration tokens
    # from being used to access protected hospital routes
    token_type = payload.get("type")
    if token_type != "hospital_access":
        raise UnauthorizedException(
            "Invalid token type. Please log in to access hospital resources."
        )

    # Step 4: Extract claims embedded in the token
    user_id_str = payload.get("sub")
    hospital_id_str = payload.get("hospital_id")
    role = payload.get("role")

    if not user_id_str or not hospital_id_str:
        raise UnauthorizedException("Token is missing required claims")

    # Step 5: Load the user from the tenant database
    # We verify the user still exists and is still active.
    # This catches cases where an admin deactivated the account
    # after the token was issued.
    try:
        result = await db.execute(
            select(HospitalUser).where(
                HospitalUser.id == uuid.UUID(user_id_str),
                HospitalUser.hospital_id == uuid.UUID(hospital_id_str),
            )
        )
        user = result.scalar_one_or_none()
    except Exception:
        raise UnauthorizedException("Could not validate credentials")

    if not user:
        raise UnauthorizedException("User account not found")

    if user.status != HospitalUserStatus.ACTIVE:
        raise ForbiddenException(
            "Your account has been deactivated. Contact your hospital administrator."
        )

    # Step 6: Return user context
    # Every protected hospital route receives this dict
    return {
        "user": user,
        "hospital_id": uuid.UUID(hospital_id_str),
        "role": role,
    }


async def require_hospital_admin(
    context: dict = Depends(get_current_hospital_user),
) -> dict:
    """
    Dependency that requires the hospital_admin role.

    Use for routes that manage hospital settings, staff, or configuration.

    Usage:
        @router.post("/staff/invite")
        async def invite_staff(
            ctx = Depends(require_hospital_admin)
        ):
            hospital_id = ctx["hospital_id"]
    """
    from app.models.tenant.hospital_user import HospitalUserRole

    if context["role"] != HospitalUserRole.HOSPITAL_ADMIN.value:
        raise ForbiddenException(
            "This action requires hospital administrator privileges"
        )
    return context


async def require_clinical_staff(
    context: dict = Depends(get_current_hospital_user),
) -> dict:
    """
    Dependency that allows clinical roles to access clinical routes.

    Permitted roles: hospital_admin, doctor, nurse, pharmacist, lab_scientist

    Use for routes that access patient records, prescriptions, lab results.

    Usage:
        @router.get("/patients/{id}/records")
        async def get_records(
            ctx = Depends(require_clinical_staff)
        ):
            ...
    """
    from app.models.tenant.hospital_user import HospitalUserRole

    clinical_roles = {
        HospitalUserRole.HOSPITAL_ADMIN.value,
        HospitalUserRole.DOCTOR.value,
        HospitalUserRole.NURSE.value,
        HospitalUserRole.PHARMACIST.value,
        HospitalUserRole.LAB_SCIENTIST.value,
    }

    if context["role"] not in clinical_roles:
        raise ForbiddenException(
            "This resource is only accessible to clinical staff"
        )
    return context