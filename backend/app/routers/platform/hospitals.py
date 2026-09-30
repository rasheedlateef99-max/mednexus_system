"""
Platform Admin — Hospital Management Router
--------------------------------------------
HTTP endpoints for the LATLOMP platform admin to manage hospitals.

Endpoints:
  GET    /platform/hospitals                  → List all hospitals (paginated)
  GET    /platform/hospitals/{id}             → Get single hospital details
  POST   /platform/hospitals/{id}/action      → Approve or reject application
  POST   /platform/hospitals/{id}/suspend     → Suspend active hospital
  POST   /platform/hospitals/{id}/reactivate  → Reactivate suspended hospital
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_platform_db
from app.core.dependencies import get_current_platform_admin, require_admin_or_above
from app.models.platform.platform_admin import PlatformAdmin
from app.schemas.hospital.registration import (
    HospitalApprovalRequest,
    HospitalSuspensionRequest,
    PaginatedHospitalList,
)
from app.services.platform.hospital_service import (
    list_hospitals,
    process_hospital_application,
    suspend_hospital,
    reactivate_hospital,
)

router = APIRouter()


@router.get(
    "/",
    response_model=PaginatedHospitalList,
    summary="List All Hospitals",
    description=(
        "Returns a paginated list of all hospitals on the platform. "
        "Supports filtering by status and searching by name or country."
    ),
)
async def get_hospitals(
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Results per page"),
    status: str | None = Query(
        default=None,
        description="Filter by status: pending_review | active | suspended | deactivated"
    ),
    search: str | None = Query(
        default=None,
        description="Search by hospital name, city, or country"
    ),
    db: AsyncSession = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin),
):
    """
    Returns all hospitals with optional filtering and search.

    Use status=pending_review to see the approval queue.
    """
    return await list_hospitals(
        db=db,
        page=page,
        per_page=per_page,
        status_filter=status,
        search=search,
    )


@router.post(
    "/{hospital_id}/action",
    summary="Approve or Reject Hospital Application",
    description=(
        "Approve or reject a hospital application in pending_review status. "
        "Requires admin role or above."
    ),
)
async def action_hospital(
    hospital_id: str,
    action_data: HospitalApprovalRequest,
    db: AsyncSession = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_admin_or_above),
):
    """
    Process a hospital application.

    action: "approve" activates the hospital workspace.
    action: "reject" deactivates the application.

    Optional notes are visible to the hospital admin.
    """
    result = await process_hospital_application(
        db=db,
        hospital_id=hospital_id,
        action_data=action_data,
        admin=admin,
    )
    return {"success": True, "data": result}


@router.post(
    "/{hospital_id}/suspend",
    summary="Suspend Hospital",
    description="Suspends an active hospital account. Requires admin role.",
)
async def suspend(
    hospital_id: str,
    suspension_data: HospitalSuspensionRequest,
    db: AsyncSession = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_admin_or_above),
):
    """Suspend an active hospital account with a required reason."""
    result = await suspend_hospital(db, hospital_id, suspension_data, admin)
    return {"success": True, "data": result}


@router.post(
    "/{hospital_id}/reactivate",
    summary="Reactivate Suspended Hospital",
    description="Reactivates a suspended hospital account. Requires admin role.",
)
async def reactivate(
    hospital_id: str,
    db: AsyncSession = Depends(get_platform_db),
    admin: PlatformAdmin = Depends(require_admin_or_above),
):
    """Reactivate a hospital that was previously suspended."""
    result = await reactivate_hospital(db, hospital_id, admin)
    return {"success": True, "data": result}