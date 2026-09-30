"""
Announcement Service
--------------------------
Business logic for creating, publishing, and reading announcements.

Targeting Resolution (_hospital_matches_announcement):
  Used both when listing announcements for a specific hospital AND
  when counting how many hospitals an announcement reaches.

    all_hospitals     → every active hospital matches
    specific_plan     → hospital matches if its active subscription's
                         plan_id equals target_value
    specific_hospital → hospital matches only if its own id equals
                         target_value
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_

from app.models.platform.hospital import Hospital
from app.models.platform.subscription import Subscription
from app.models.platform.platform_admin import PlatformAdmin
from app.models.platform.announcement import (
    Announcement, AnnouncementRead, AnnouncementTargetType, AnnouncementSeverity,
)
from app.schemas.platform.announcement import (
    AnnouncementCreateRequest, AnnouncementUpdateRequest,
    AnnouncementResponse, AnnouncementListResponse,
    HospitalAnnouncementResponse, HospitalAnnouncementListResponse,
)
from app.core.exceptions import NotFoundException, ValidationException, ForbiddenException


async def _get_admin_name(db: AsyncSession, admin_id: uuid.UUID) -> str:
    result = await db.execute(select(PlatformAdmin.full_name).where(PlatformAdmin.id == admin_id))
    row = result.first()
    return row[0] if row else ""


async def _get_read_count(db: AsyncSession, announcement_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(AnnouncementRead.id)).where(
            AnnouncementRead.announcement_id == announcement_id
        )
    )
    return result.scalar() or 0


async def _build_response(db: AsyncSession, ann: Announcement) -> AnnouncementResponse:
    response = AnnouncementResponse.model_validate(ann)
    response.created_by_name = await _get_admin_name(db, ann.created_by)
    response.read_count = await _get_read_count(db, ann.id)
    return response


# ── Core Service Functions (Platform Admin Side) ──────────────────────────────

async def create_announcement(
    db: AsyncSession,
    creating_admin: PlatformAdmin,
    data: AnnouncementCreateRequest,
) -> AnnouncementResponse:
    target_type = AnnouncementTargetType(data.target_type)

    if target_type in (AnnouncementTargetType.SPECIFIC_PLAN, AnnouncementTargetType.SPECIFIC_HOSPITAL):
        if not data.target_value:
            raise ValidationException(
                f"target_value is required when target_type is '{data.target_type}'."
            )
        try:
            uuid.UUID(data.target_value)
        except ValueError:
            raise ValidationException("target_value must be a valid UUID string.")

    now = datetime.now(timezone.utc)
    announcement = Announcement(
        title=data.title,
        body=data.body,
        severity=AnnouncementSeverity(data.severity),
        target_type=target_type,
        target_value=data.target_value,
        expires_at=data.expires_at,
        created_by=creating_admin.id,
        is_published=data.publish_immediately,
        published_at=now if data.publish_immediately else None,
    )
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)

    return await _build_response(db, announcement)


async def update_announcement(
    db: AsyncSession, announcement_id: uuid.UUID, data: AnnouncementUpdateRequest,
) -> AnnouncementResponse:
    """Only drafts (is_published=False) can be edited — published content is locked."""
    result = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
    announcement = result.scalar_one_or_none()
    if not announcement:
        raise NotFoundException("Announcement")

    if announcement.is_published:
        raise ValidationException(
            "Cannot edit a published announcement. Unpublish it first, or create a new one."
        )

    if data.title is not None:
        announcement.title = data.title
    if data.body is not None:
        announcement.body = data.body
    if data.severity is not None:
        announcement.severity = AnnouncementSeverity(data.severity)
    if data.target_type is not None:
        announcement.target_type = AnnouncementTargetType(data.target_type)
    if data.target_value is not None:
        announcement.target_value = data.target_value
    if data.expires_at is not None:
        announcement.expires_at = data.expires_at

    await db.commit()
    await db.refresh(announcement)
    return await _build_response(db, announcement)


async def publish_announcement(db: AsyncSession, announcement_id: uuid.UUID) -> AnnouncementResponse:
    result = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
    announcement = result.scalar_one_or_none()
    if not announcement:
        raise NotFoundException("Announcement")

    if announcement.is_published:
        raise ValidationException("This announcement is already published.")

    announcement.is_published = True
    announcement.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(announcement)
    return await _build_response(db, announcement)


async def unpublish_announcement(db: AsyncSession, announcement_id: uuid.UUID) -> AnnouncementResponse:
    """Pulls a published announcement back to draft — e.g. published in error."""
    result = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
    announcement = result.scalar_one_or_none()
    if not announcement:
        raise NotFoundException("Announcement")

    if not announcement.is_published:
        raise ValidationException("This announcement is not published.")

    announcement.is_published = False
    announcement.published_at = None
    await db.commit()
    await db.refresh(announcement)
    return await _build_response(db, announcement)


async def delete_announcement(db: AsyncSession, announcement_id: uuid.UUID) -> dict:
    result = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
    announcement = result.scalar_one_or_none()
    if not announcement:
        raise NotFoundException("Announcement")

    await db.execute(
        AnnouncementRead.__table__.delete().where(AnnouncementRead.announcement_id == announcement_id)
    )
    await db.delete(announcement)
    await db.commit()
    return {"message": "Announcement deleted."}


async def list_announcements(
    db: AsyncSession, page: int = 1, per_page: int = 20, published_only: bool | None = None,
) -> AnnouncementListResponse:
    query = select(Announcement).order_by(Announcement.created_at.desc())
    if published_only is not None:
        query = query.where(Announcement.is_published == published_only)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    items = [await _build_response(db, a) for a in result.scalars().all()]

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return AnnouncementListResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)


# ── Hospital-Facing Functions ─────────────────────────────────────────────────

async def _hospital_matches_announcement(
    db: AsyncSession, hospital_id: uuid.UUID, announcement: Announcement,
) -> bool:
    if announcement.target_type == AnnouncementTargetType.ALL_HOSPITALS:
        return True
    if announcement.target_type == AnnouncementTargetType.SPECIFIC_HOSPITAL:
        return str(hospital_id) == announcement.target_value
    if announcement.target_type == AnnouncementTargetType.SPECIFIC_PLAN:
        sub_result = await db.execute(
            select(Subscription.plan_id).where(Subscription.hospital_id == hospital_id)
        )
        row = sub_result.first()
        return row is not None and str(row[0]) == announcement.target_value
    return False


async def get_announcements_for_hospital(
    db: AsyncSession, hospital_id: uuid.UUID,
) -> HospitalAnnouncementListResponse:
    """
    Returns all published, non-expired announcements that target this
    hospital, with per-announcement read status.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Announcement)
        .where(
            Announcement.is_published == True,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
        )
        .order_by(Announcement.published_at.desc())
    )
    all_published = result.scalars().all()

    matching = [
        a for a in all_published
        if await _hospital_matches_announcement(db, hospital_id, a)
    ]

    read_result = await db.execute(
        select(AnnouncementRead.announcement_id).where(
            AnnouncementRead.hospital_id == hospital_id,
            AnnouncementRead.announcement_id.in_([a.id for a in matching]) if matching else False,
        )
    )
    read_ids = {row[0] for row in read_result.all()}

    items = []
    unread_count = 0
    for a in matching:
        is_read = a.id in read_ids
        if not is_read:
            unread_count += 1
        items.append(
            HospitalAnnouncementResponse(
                id=a.id, title=a.title, body=a.body, severity=a.severity.value,
                published_at=a.published_at, expires_at=a.expires_at, is_read=is_read,
            )
        )

    return HospitalAnnouncementListResponse(items=items, unread_count=unread_count)


async def mark_announcement_read(
    db: AsyncSession, announcement_id: uuid.UUID, hospital_id: uuid.UUID, reading_user_id: uuid.UUID,
) -> dict:
    result = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
    announcement = result.scalar_one_or_none()
    if not announcement or not announcement.is_published:
        raise NotFoundException("Announcement")

    if not await _hospital_matches_announcement(db, hospital_id, announcement):
        raise ForbiddenException("This announcement is not targeted at your hospital.")

    existing = await db.execute(
        select(AnnouncementRead).where(
            AnnouncementRead.announcement_id == announcement_id,
            AnnouncementRead.hospital_id == hospital_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"message": "Already marked as read."}

    read_record = AnnouncementRead(
        announcement_id=announcement_id, hospital_id=hospital_id, read_by=reading_user_id,
    )
    db.add(read_record)
    await db.commit()
    return {"message": "Marked as read."}