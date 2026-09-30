"""
Hospital Announcement Router
------------------------------------
Hospital-side: view announcements targeted at this hospital, mark as read.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_platform_db
from app.core.dependencies import get_current_hospital_user
from app.schemas.platform.announcement import HospitalAnnouncementListResponse
from app.services.platform.announcement_service import (
    get_announcements_for_hospital, mark_announcement_read,
)

router = APIRouter()


@router.get(
    "/",
    response_model=HospitalAnnouncementListResponse,
    summary="My Announcements",
    description="Returns all published, non-expired announcements targeted at your hospital, with read status.",
)
async def list_mine(
    platform_db: AsyncSession = Depends(get_platform_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_announcements_for_hospital(platform_db, ctx["hospital_id"])


@router.post(
    "/{announcement_id}/read",
    summary="Mark as Read",
    description="Marks an announcement as read for your hospital.",
)
async def mark_read(
    announcement_id: UUID,
    platform_db: AsyncSession = Depends(get_platform_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await mark_announcement_read(
        platform_db, announcement_id, ctx["hospital_id"], ctx["user"].id,
    )