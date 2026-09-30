"""
Platform Announcement Router
------------------------------------
Platform admin: create, edit, publish/unpublish, list, delete announcements.

M20 hook: publishing an announcement fires in-app notifications to all
active hospital staff in the targeted hospitals via the tenant DB.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.database import get_platform_db, get_tenant_db
from app.core.dependencies import get_current_platform_admin
from app.models.platform.hospital import Hospital, HospitalStatus
from app.models.platform.subscription import Subscription, SubscriptionStatus
from app.models.tenant.hospital_user import HospitalUser
from app.schemas.platform.announcement import (
    AnnouncementCreateRequest, AnnouncementUpdateRequest,
    AnnouncementResponse, AnnouncementListResponse,
)
from app.services.platform.announcement_service import (
    create_announcement, update_announcement, publish_announcement,
    unpublish_announcement, delete_announcement, list_announcements,
)
from app.services.hospital.notification_service import (
    create_notifications_bulk, NotificationEventType,
)

router = APIRouter()


@router.post(
    "/",
    response_model=AnnouncementResponse,
    status_code=201,
    summary="Create Announcement",
    description="Creates a new announcement, as a draft unless publish_immediately is set.",
)
async def create(
    data: AnnouncementCreateRequest,
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await create_announcement(db, admin, data)


@router.get(
    "/",
    response_model=AnnouncementListResponse,
    summary="List Announcements",
    description="Returns all announcements. Optionally filter by published status.",
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    published_only: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await list_announcements(db, page, per_page, published_only)


@router.patch(
    "/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="Edit Draft Announcement",
    description="Edits an unpublished announcement. Published announcements cannot be edited.",
)
async def update(
    announcement_id: UUID,
    data: AnnouncementUpdateRequest,
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await update_announcement(db, announcement_id, data)


@router.post(
    "/{announcement_id}/publish",
    response_model=AnnouncementResponse,
    summary="Publish Announcement",
    description="Publishes a draft announcement and fires in-app notifications to targeted hospital staff.",
)
async def publish(
    announcement_id: UUID,
    db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    admin=Depends(get_current_platform_admin),
):
    result = await publish_announcement(db, announcement_id)

    # M20 hook: notify active staff in targeted hospitals
    try:
        from app.models.platform.announcement import Announcement, AnnouncementTargetType
        ann_result = await db.execute(
            select(Announcement).where(Announcement.id == announcement_id)
        )
        announcement = ann_result.scalar_one_or_none()

        if announcement:
            # Determine which hospital_ids are targeted
            targeted_hospital_ids: list[UUID] = []

            if announcement.target_type == AnnouncementTargetType.ALL_HOSPITALS:
                h_result = await db.execute(
                    select(Hospital.id).where(Hospital.status == HospitalStatus.ACTIVE)
                )
                targeted_hospital_ids = [row[0] for row in h_result.all()]

            elif announcement.target_type == AnnouncementTargetType.SPECIFIC_HOSPITAL:
                if announcement.target_value:
                    targeted_hospital_ids = [UUID(announcement.target_value)]

            elif announcement.target_type == AnnouncementTargetType.SPECIFIC_PLAN:
                if announcement.target_value:
                    sub_result = await db.execute(
                        select(Subscription.hospital_id).where(
                            Subscription.plan_id == UUID(announcement.target_value),
                            Subscription.status == SubscriptionStatus.ACTIVE,
                        )
                    )
                    targeted_hospital_ids = [row[0] for row in sub_result.all()]

            # For each targeted hospital, notify all active staff
            for hospital_id in targeted_hospital_ids:
                staff_result = await tenant_db.execute(
                    select(HospitalUser.id).where(
                        HospitalUser.hospital_id == hospital_id,
                    )
                )
                recipient_ids = [row[0] for row in staff_result.all()]
                if recipient_ids:
                    await create_notifications_bulk(
                        db=tenant_db,
                        hospital_id=hospital_id,
                        recipient_ids=recipient_ids,
                        event_type=NotificationEventType.ANNOUNCEMENT_PUBLISHED,
                        title=f"New Announcement: {announcement.title}",
                        body=announcement.body[:300],
                        link_path=f"/announcements/{announcement_id}",
                        source_type="announcement",
                        source_id=announcement_id,
                    )
    except Exception:
        # Notification delivery failure must never roll back the publish action
        import logging
        logging.getLogger(__name__).warning(
            "Failed to send announcement notifications for %s", announcement_id,
            exc_info=True,
        )

    return result


@router.post(
    "/{announcement_id}/unpublish",
    response_model=AnnouncementResponse,
    summary="Unpublish Announcement",
    description="Pulls a published announcement back to draft.",
)
async def unpublish(
    announcement_id: UUID,
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await unpublish_announcement(db, announcement_id)


@router.delete(
    "/{announcement_id}",
    summary="Delete Announcement",
    description="Permanently deletes an announcement and its read records.",
)
async def delete(
    announcement_id: UUID,
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await delete_announcement(db, announcement_id)