"""
Notification Service
--------------------------
Creates, lists, and marks-read in-app notifications.
Also handles email delivery for high-priority event types
(critical lab results, support ticket updates).

Two categories of functions:

  WRITE (called by other services as hooks):
    create_notification()          — single recipient
    create_notifications_bulk()    — multiple recipients (announcements)

  READ (called by the router):
    list_notifications()           — paginated list for one user
    mark_notification_read()       — mark one as read
    mark_all_notifications_read()  — bulk mark-read

Email delivery:
  Only for CRITICAL_LAB_RESULT and SUPPORT_TICKET_* events.
  Uses Resend (already configured from Milestone 4).
  Fires-and-forgets — an email delivery failure never raises an
  exception that would roll back the triggering action. The
  in-app notification row is always created regardless of email
  delivery success.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.tenant.notification import Notification, NotificationEventType
from app.models.tenant.hospital_user import HospitalUser
from app.schemas.hospital.notification import NotificationResponse, NotificationListResponse
from app.core.exceptions import NotFoundException
from app.config import settings
import logging

logger = logging.getLogger(__name__)

# Event types that also trigger an email
EMAIL_NOTIFY_TYPES = {
    NotificationEventType.CRITICAL_LAB_RESULT,
    NotificationEventType.SUPPORT_TICKET_REPLY,
    NotificationEventType.SUPPORT_TICKET_RESOLVED,
    NotificationEventType.SUPPORT_TICKET_CLOSED,
}


async def _send_notification_email(
    recipient_email: str, recipient_name: str, title: str, body: str,
) -> None:
    """
    Sends a notification email via Resend.
    Fire-and-forget — logs failures but never raises.
    """
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping notification email")
        return

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": settings.FROM_EMAIL,
                    "to": [recipient_email],
                    "subject": title,
                    "html": f"""
                        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                            <h2 style="color: #2563eb;">MedNexus Notification</h2>
                            <p>Dear {recipient_name},</p>
                            <p>{body}</p>
                            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
                            <p style="font-size: 12px; color: #64748b;">
                                This is an automated notification from MedNexus.
                                Please log in to your hospital workspace to view full details.
                            </p>
                        </div>
                    """,
                },
                timeout=10.0,
            )
            if response.status_code not in (200, 201):
                logger.warning(
                    "Notification email delivery failed: status=%s body=%s",
                    response.status_code, response.text[:200],
                )
    except Exception as e:
        logger.warning("Notification email delivery exception: %s", str(e))


# ── Write Functions (called by service hooks) ─────────────────────────────────

async def create_notification(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    recipient_id: uuid.UUID,
    event_type: NotificationEventType,
    title: str,
    body: str,
    link_path: str | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
) -> Notification:
    """
    Creates one in-app notification row.
    If the event type is in EMAIL_NOTIFY_TYPES, also attempts email delivery.
    Always returns the created Notification — email failure does not raise.
    """
    notification = Notification(
        hospital_id=hospital_id,
        recipient_id=recipient_id,
        event_type=event_type,
        title=title,
        body=body,
        link_path=link_path,
        source_type=source_type,
        source_id=source_id,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    if event_type in EMAIL_NOTIFY_TYPES:
        user_result = await db.execute(
            select(HospitalUser.full_name, HospitalUser.email).where(HospitalUser.id == recipient_id)
        )
        row = user_result.first()
        if row and row[1]:
            await _send_notification_email(row[1], row[0], title, body)

    return notification


async def create_notifications_bulk(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    recipient_ids: list[uuid.UUID],
    event_type: NotificationEventType,
    title: str,
    body: str,
    link_path: str | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
) -> int:
    """
    Creates one notification row per recipient. Used for announcements
    where many users receive the same event simultaneously.
    Returns the count of notifications created.
    No email sent for bulk notifications (announcements are in-app only).
    """
    for rid in recipient_ids:
        notification = Notification(
            hospital_id=hospital_id,
            recipient_id=rid,
            event_type=event_type,
            title=title,
            body=body,
            link_path=link_path,
            source_type=source_type,
            source_id=source_id,
        )
        db.add(notification)

    await db.commit()
    return len(recipient_ids)


# ── Read Functions (called by the router) ────────────────────────────────────

async def list_notifications(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    recipient_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    unread_only: bool = False,
) -> NotificationListResponse:
    query = (
        select(Notification)
        .where(
            Notification.hospital_id == hospital_id,
            Notification.recipient_id == recipient_id,
        )
        .order_by(Notification.created_at.desc())
    )
    if unread_only:
        query = query.where(Notification.is_read == False)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    unread_result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.hospital_id == hospital_id,
            Notification.recipient_id == recipient_id,
            Notification.is_read == False,
        )
    )
    unread_count = unread_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    items = [NotificationResponse.model_validate(n) for n in result.scalars().all()]

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return NotificationListResponse(
        items=items, total=total, unread_count=unread_count,
        page=page, per_page=per_page, pages=pages,
    )


async def mark_notification_read(
    db: AsyncSession, hospital_id: uuid.UUID, notification_id: uuid.UUID, user_id: uuid.UUID,
) -> NotificationResponse:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.hospital_id == hospital_id,
            Notification.recipient_id == user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise NotFoundException("Notification")

    notification.is_read = True
    await db.commit()
    await db.refresh(notification)
    return NotificationResponse.model_validate(notification)


async def mark_all_notifications_read(
    db: AsyncSession, hospital_id: uuid.UUID, user_id: uuid.UUID,
) -> dict:
    result = await db.execute(
        select(Notification).where(
            Notification.hospital_id == hospital_id,
            Notification.recipient_id == user_id,
            Notification.is_read == False,
        )
    )
    unread = result.scalars().all()
    for n in unread:
        n.is_read = True
    await db.commit()
    return {"marked_read": len(unread)}