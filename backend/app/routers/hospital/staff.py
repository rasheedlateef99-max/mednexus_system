"""
Hospital Staff Router
-----------------------
HTTP endpoints for staff management.

Endpoints:
  POST  /hospital/staff/invite              → Invite a new staff member
  POST  /hospital/staff/accept-invitation   → Accept an invitation (no auth)
  GET   /hospital/staff/                    → List all staff
  GET   /hospital/staff/{id}                → Get staff member profile
  PUT   /hospital/staff/{id}                → Update staff member
  POST  /hospital/staff/{id}/deactivate     → Deactivate staff member
  POST  /hospital/staff/{id}/reactivate     → Reactivate staff member
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user, require_hospital_admin
from app.schemas.hospital.staff import (
    StaffInviteRequest,
    StaffUpdateRequest,
    InvitationAcceptRequest,
    StaffMemberResponse,
    StaffListResponse,
    InvitationResponse,
)
from app.services.hospital.staff_service import (
    invite_staff_member,
    accept_invitation,
    list_staff,
    get_staff_member,
    update_staff_member,
    deactivate_staff_member,
    reactivate_staff_member,
)

router = APIRouter()


@router.post(
    "/invite",
    response_model=InvitationResponse,
    summary="Invite Staff Member",
    description=(
        "Invites a new staff member to the hospital workspace. "
        "Creates their account and sends an invitation email. "
        "Requires hospital admin role."
    ),
)
async def invite(
    data: StaffInviteRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_hospital_admin),
):
    return await invite_staff_member(
        db=db,
        hospital_id=ctx["hospital_id"],
        inviting_admin=ctx["user"],
        data=data,
    )


@router.post(
    "/accept-invitation",
    response_model=StaffMemberResponse,
    summary="Accept Staff Invitation",
    description=(
        "Accepts a staff invitation using the token from the invitation email. "
        "Sets the staff member's password and activates their account. "
        "No authentication required — this is called before the user has a login."
    ),
)
async def accept_invite(
    data: InvitationAcceptRequest,
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Public endpoint — no JWT required.

    Called when the invited staff member clicks their email link
    and submits the password setup form on the frontend.
    """
    return await accept_invitation(db=db, data=data)


@router.get(
    "/",
    response_model=StaffListResponse,
    summary="List Staff Members",
    description=(
        "Returns a paginated list of all staff members in the hospital. "
        "Supports filtering by role, department, and status."
    ),
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    role: str | None = Query(default=None, description="Filter by role"),
    department_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None, description="active | invited | inactive"),
    search: str | None = Query(default=None, description="Search by name or email"),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_staff(
        db=db,
        hospital_id=ctx["hospital_id"],
        page=page,
        per_page=per_page,
        role_filter=role,
        department_id=department_id,
        status_filter=status,
        search=search,
    )


@router.get(
    "/{staff_id}",
    response_model=StaffMemberResponse,
    summary="Get Staff Member",
    description="Returns a single staff member's profile.",
)
async def get_one(
    staff_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_staff_member(db, ctx["hospital_id"], staff_id)


@router.put(
    "/{staff_id}",
    response_model=StaffMemberResponse,
    summary="Update Staff Member",
    description=(
        "Updates a staff member's profile. "
        "Admins can update any staff member. "
        "Staff can update their own profile (limited fields)."
    ),
)
async def update(
    staff_id: UUID,
    data: StaffUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await update_staff_member(
        db=db,
        hospital_id=ctx["hospital_id"],
        staff_id=staff_id,
        data=data,
        requesting_user=ctx["user"],
    )


@router.post(
    "/{staff_id}/deactivate",
    summary="Deactivate Staff Member",
    description=(
        "Deactivates a staff member's account. "
        "They will no longer be able to log in. "
        "Requires hospital admin role."
    ),
)
async def deactivate(
    staff_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_hospital_admin),
):
    result = await deactivate_staff_member(
        db=db,
        hospital_id=ctx["hospital_id"],
        staff_id=staff_id,
        requesting_user=ctx["user"],
    )
    return {"success": True, "data": result}


@router.post(
    "/{staff_id}/reactivate",
    summary="Reactivate Staff Member",
    description="Reactivates a previously deactivated staff member. Requires hospital admin role.",
)
async def reactivate(
    staff_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_hospital_admin),
):
    result = await reactivate_staff_member(
        db=db,
        hospital_id=ctx["hospital_id"],
        staff_id=staff_id,
    )
    return {"success": True, "data": result}