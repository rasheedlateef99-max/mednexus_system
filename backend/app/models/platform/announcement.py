"""
Announcement Models (Platform Database)
---------------------------------------------
Two models for the platform-to-hospital broadcast channel:

  Announcement       → the broadcast (title, body, targeting, publish state)
  AnnouncementRead    → per-hospital read tracking

Targeting:
  target_type + target_value together define who sees an announcement.

    all_hospitals     → target_value is None, every active hospital sees it
    specific_plan     → target_value holds a Plan UUID (as string)
    specific_hospital → target_value holds a Hospital UUID (as string)

  target_value is stored as a plain string rather than a typed UUID
  column because its meaning depends on target_type — same pattern as
  author_id/author_type in the support ticket system. Resolved and
  validated in the service layer at creation time and at read time.

Draft Workflow:
  is_published=False means the announcement exists but is not visible
  to any hospital yet — lets an admin write and review before broadcast.
  published_at is set the moment is_published flips to True.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Enum as SAEnum, func, UUID, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import PlatformBase


class AnnouncementTargetType(str, enum.Enum):
    ALL_HOSPITALS = "all_hospitals"
    SPECIFIC_PLAN = "specific_plan"
    SPECIFIC_HOSPITAL = "specific_hospital"


class AnnouncementSeverity(str, enum.Enum):
    """Visual/urgency hint for the frontend — info banner vs urgent alert."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Announcement(PlatformBase):
    __tablename__ = "announcements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    severity: Mapped[AnnouncementSeverity] = mapped_column(
        SAEnum(AnnouncementSeverity), nullable=False, default=AnnouncementSeverity.INFO,
    )

    target_type: Mapped[AnnouncementTargetType] = mapped_column(
        SAEnum(AnnouncementTargetType), nullable=False, default=AnnouncementTargetType.ALL_HOSPITALS,
    )
    target_value: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Platform admin who created this (no real FK across rows that change ownership; plain UUID)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Optional auto-expiry — past this date, stop showing it as "new" (frontend concern;
    # we still keep the row for history)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Announcement id={self.id} title='{self.title}' published={self.is_published}>"


class AnnouncementRead(PlatformBase):
    """
    One row per (announcement, hospital) once that hospital has viewed it.
    Absence of a row = unread. No row is ever created for "unread" — we
    only insert on the read action itself.
    """
    __tablename__ = "announcement_reads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    announcement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # The specific hospital user who read it (tenant DB UUID, no real FK — same
    # cross-database pattern as ticket message authorship)
    read_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AnnouncementRead announcement_id={self.announcement_id} hospital_id={self.hospital_id}>"