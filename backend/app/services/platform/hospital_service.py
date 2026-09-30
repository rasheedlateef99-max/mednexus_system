"""
Platform Hospital Management Service
---------------------------------------
Business logic for platform admin management of hospitals.

This service is used by:
  1. Platform admin dashboard (approve/reject/suspend hospitals)
  2. Hospital auth service (check hospital status during login)

All write operations here change hospital status in the platform database.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.platform.hospital import Hospital, HospitalStatus
from app.models.platform.subscription import Subscription
from app.models.platform.platform_admin import PlatformAdmin
from app.schemas.hospital.registration import (
    HospitalApprovalRequest,
    HospitalSuspensionRequest,
    HospitalListItem,
    PaginatedHospitalList,
)
from app.core.exceptions import (
    NotFoundException,
    ForbiddenException,
    ValidationException,
)


# ── Helper Functions (used by auth service) ───────────────────────────────────

# These functions need their own database connection to the platform DB.
# We create a lightweight session here rather than depending on request context.

async def get_hospital_status(hospital_id: str) -> str:
    """
    Returns the current status of a hospital.

    Used by the hospital auth service to determine which token to issue
    during the Google OAuth callback.

    Args:
        hospital_id: UUID string

    Returns:
        Status string: pending_review | active | suspended | deactivated
    """
    from app.database import PlatformSessionLocal

    async with PlatformSessionLocal() as db:
        result = await db.execute(
            select(Hospital.status).where(
                Hospital.id == uuid.UUID(hospital_id)
            )
        )
        status = result.scalar_one_or_none()
        return status.value if status else "not_found"


async def get_hospital_name(hospital_id: str) -> str:
    """
    Returns the name of a hospital by ID.

    Args:
        hospital_id: UUID string

    Returns:
        Hospital name string, or "Unknown Hospital" if not found
    """
    from app.database import PlatformSessionLocal

    async with PlatformSessionLocal() as db:
        result = await db.execute(
            select(Hospital.name).where(
                Hospital.id == uuid.UUID(hospital_id)
            )
        )
        name = result.scalar_one_or_none()
        return name or "Unknown Hospital"


# ── Platform Admin: List Hospitals ────────────────────────────────────────────

async def list_hospitals(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 20,
    status_filter: str | None = None,
    search: str | None = None,
) -> PaginatedHospitalList:
    """
    Returns a paginated list of hospitals for the platform admin dashboard.

    Args:
        db:            Platform database session
        page:          Page number (1-indexed)
        per_page:      Results per page
        status_filter: Filter by status (pending_review, active, etc.)
        search:        Search by hospital name or country

    Returns:
        PaginatedHospitalList
    """
    query = select(Hospital).order_by(Hospital.created_at.desc())

    # Apply status filter
    if status_filter:
        try:
            status_enum = HospitalStatus(status_filter)
            query = query.where(Hospital.status == status_enum)
        except ValueError:
            pass  # Invalid status filter — ignore it

    # Apply search
    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                Hospital.name.ilike(search_term),
                Hospital.country.ilike(search_term),
                Hospital.city.ilike(search_term),
            )
        )

    # Count total matching records
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    hospitals = result.scalars().all()

    # Build list items with subscription status
    items = []
    for hospital in hospitals:
        sub_result = await db.execute(
            select(Subscription.status).where(
                Subscription.hospital_id == hospital.id
            )
        )
        sub_status = sub_result.scalar_one_or_none()

        items.append(
            HospitalListItem(
                id=hospital.id,
                name=hospital.name,
                country=hospital.country,
                status=hospital.status.value,
                created_at=hospital.created_at,
                approved_at=hospital.approved_at,
                subscription_status=sub_status.value if sub_status else None,
            )
        )

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return PaginatedHospitalList(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ── Platform Admin: Approve or Reject ─────────────────────────────────────────

async def process_hospital_application(
    db: AsyncSession,
    hospital_id: str,
    action_data: HospitalApprovalRequest,
    admin: PlatformAdmin,
) -> dict:
    """
    Approves or rejects a hospital application.

    Only hospitals with status pending_review can be actioned here.

    Args:
        db:          Platform database session
        hospital_id: UUID string of the hospital
        action_data: Action (approve/reject) and optional notes
        admin:       The platform admin performing the action

    Returns:
        Success message dict
    """
    result = await db.execute(
        select(Hospital).where(Hospital.id == uuid.UUID(hospital_id))
    )
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise NotFoundException("Hospital")

    if hospital.status != HospitalStatus.PENDING_REVIEW:
        raise ValidationException(
            f"This hospital cannot be actioned. "
            f"Current status: {hospital.status.value}. "
            f"Only hospitals with status 'pending_review' can be approved or rejected."
        )

    now = datetime.now(timezone.utc)

    if action_data.action == "approve":
        hospital.status = HospitalStatus.ACTIVE
        hospital.approved_by = admin.id
        hospital.approved_at = now
        hospital.review_notes = action_data.notes
        await db.commit()
        return {
            "message": f"Hospital '{hospital.name}' has been approved and is now active.",
            "hospital_id": str(hospital.id),
            "status": "active",
        }
    else:
        # Rejected — we delete the record or mark as deactivated
        # We deactivate rather than delete to preserve the audit trail
        hospital.status = HospitalStatus.DEACTIVATED
        hospital.review_notes = action_data.notes
        hospital.approved_by = admin.id
        hospital.approved_at = now
        await db.commit()
        return {
            "message": f"Hospital '{hospital.name}' application has been rejected.",
            "hospital_id": str(hospital.id),
            "status": "deactivated",
        }


# ── Platform Admin: Suspend / Reactivate ──────────────────────────────────────

async def suspend_hospital(
    db: AsyncSession,
    hospital_id: str,
    suspension_data: HospitalSuspensionRequest,
    admin: PlatformAdmin,
) -> dict:
    """
    Suspends an active hospital account.

    Args:
        db:              Platform database session
        hospital_id:     UUID string
        suspension_data: Reason for suspension
        admin:           The platform admin performing the action

    Returns:
        Success message dict
    """
    result = await db.execute(
        select(Hospital).where(Hospital.id == uuid.UUID(hospital_id))
    )
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise NotFoundException("Hospital")

    if hospital.status != HospitalStatus.ACTIVE:
        raise ValidationException(
            "Only active hospitals can be suspended."
        )

    hospital.status = HospitalStatus.SUSPENDED
    hospital.suspended_at = datetime.now(timezone.utc)
    hospital.suspension_reason = suspension_data.reason
    await db.commit()

    return {
        "message": f"Hospital '{hospital.name}' has been suspended.",
        "hospital_id": str(hospital.id),
        "status": "suspended",
    }


async def reactivate_hospital(
    db: AsyncSession,
    hospital_id: str,
    admin: PlatformAdmin,
) -> dict:
    """
    Reactivates a suspended hospital account.

    Args:
        db:          Platform database session
        hospital_id: UUID string
        admin:       The platform admin performing the action

    Returns:
        Success message dict
    """
    result = await db.execute(
        select(Hospital).where(Hospital.id == uuid.UUID(hospital_id))
    )
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise NotFoundException("Hospital")

    if hospital.status != HospitalStatus.SUSPENDED:
        raise ValidationException(
            "Only suspended hospitals can be reactivated."
        )

    hospital.status = HospitalStatus.ACTIVE
    hospital.suspended_at = None
    hospital.suspension_reason = None
    await db.commit()

    return {
        "message": f"Hospital '{hospital.name}' has been reactivated.",
        "hospital_id": str(hospital.id),
        "status": "active",
    }