"""
Notifications Router
--------------------------
In-app notification list and read-management for hospital staff.
Every route scopes automatically to the authenticated user's
hospital_id and user id — users only ever see their own notifications.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user
from app.schemas.hospital.notification import NotificationListResponse, NotificationResponse
from app.services.hospital.notification_service import (
    list_notifications, mark_notification_read, mark_all_notifications_read,
)

router = APIRouter()


@router.get(
    "/",
    response_model=NotificationListResponse,
    summary="My Notifications",
    description="Returns notifications for the current user, newest first. Optionally filter to unread only.",
)
async def list_mine(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_notifications(db, ctx["hospital_id"], ctx["user"].id, page, per_page, unread_only)


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark Notification as Read",
    description="Marks a single notification as read. Only the recipient can mark their own notifications.",
)
async def mark_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await mark_notification_read(db, ctx["hospital_id"], notification_id, ctx["user"].id)


@router.post(
    "/read-all",
    summary="Mark All Notifications as Read",
    description="Marks all unread notifications for the current user as read.",
)
async def mark_all_read(
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await mark_all_notifications_read(db, ctx["hospital_id"], ctx["user"].id)