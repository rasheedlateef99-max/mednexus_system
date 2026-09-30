"""
Hospital Registration Router
--------------------------------
HTTP endpoints for the hospital onboarding flow.

Endpoints:
  GET  /hospital/registration/plans          → List available subscription plans
  POST /hospital/registration/submit         → Submit hospital registration form
  GET  /hospital/registration/status         → Check application status
"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.database import get_tenant_db, get_platform_db
from app.schemas.hospital.registration import (
    HospitalRegistrationRequest,
    HospitalRegistrationResponse,
    HospitalStatusResponse,
    PlanSummary,
)
from app.services.hospital.registration_service import (
    list_available_plans,
    register_hospital,
    get_registration_status,
)

router = APIRouter()


@router.get(
    "/plans",
    response_model=list[PlanSummary],
    summary="List Available Subscription Plans",
    description=(
        "Returns all publicly available subscription plans. "
        "Called during the plan selection step of hospital onboarding. "
        "No authentication required."
    ),
)
async def get_plans(
    platform_db: AsyncSession = Depends(get_platform_db),
):
    """
    Returns available subscription plans for display on the pricing page.
    This endpoint is public — no token required.
    """
    plans = await list_available_plans(platform_db)
    return plans


@router.post(
    "/submit",
    response_model=HospitalRegistrationResponse,
    summary="Submit Hospital Registration",
    description=(
        "Submit hospital information after Google sign-in and plan selection. "
        "Requires a registration token from the Google OAuth callback. "
        "Creates a hospital application with status: pending_review."
    ),
)
async def submit_registration(
    data: HospitalRegistrationRequest,
    authorization: Annotated[str, Header()] = "",
    platform_db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
):
    """
    Submit the hospital registration form.

    Requires the registration token received from Google OAuth callback.
    Pass it as: Authorization: Bearer <registration_token>

    After submission, the hospital will be in pending_review status
    until approved by a platform administrator.
    """
    # Extract token from Authorization header
    registration_token = ""
    if authorization.startswith("Bearer "):
        registration_token = authorization[7:]

    if not registration_token:
        from app.core.exceptions import UnauthorizedException
        raise UnauthorizedException(
            "Registration token required. Please sign in with Google first."
        )

    return await register_hospital(
        platform_db=platform_db,
        tenant_db=tenant_db,
        registration_token=registration_token,
        data=data,
    )


@router.get(
    "/status",
    response_model=HospitalStatusResponse,
    summary="Check Registration Status",
    description=(
        "Returns the current status of a hospital registration application. "
        "Requires a registration token. "
        "Use this to poll for approval after submitting the registration form."
    ),
)
async def check_registration_status(
    authorization: Annotated[str, Header()] = "",
    platform_db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
):
    """
    Check the status of your hospital's registration application.

    Pass your registration token as: Authorization: Bearer <registration_token>
    """
    registration_token = ""
    if authorization.startswith("Bearer "):
        registration_token = authorization[7:]

    if not registration_token:
        from app.core.exceptions import UnauthorizedException
        raise UnauthorizedException(
            "Registration token required. Please sign in with Google first."
        )

    return await get_registration_status(
        platform_db=platform_db,
        tenant_db=tenant_db,
        registration_token=registration_token,
    )