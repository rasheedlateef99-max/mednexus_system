"""
Support Ticket Models (Platform Database)
----------------------------------------------
Two models representing the hospital-to-LATLOMP support channel:

  SupportTicket   → the ticket header (subject, category, priority, status)
  TicketMessage   → threaded conversation, many messages per ticket

Cross-Database Authorship:
  A message can be authored by either a hospital user (lives in the
  TENANT database) or a platform admin (lives in the PLATFORM database).
  Since these are physically separate databases, no real foreign key
  can span them. author_id is a plain UUID; author_type disambiguates
  which side it belongs to. The service layer is responsible for
  resolving the actual name/identity at read time by querying the
  correct database based on author_type — same cross-database
  reference pattern used for appointment_id/prescription_id in the
  billing module.

Status Lifecycle:
  open        → New, awaiting first admin response
  in_progress → Admin has responded at least once
  resolved    → Admin marked resolved, awaiting hospital confirmation
  closed      → Terminal
  reopened    → Hospital replied after resolved/closed (transient —
                service layer immediately moves this to in_progress;
                kept as a distinct enum value for event/audit clarity
                in case we log status transitions later)
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Enum as SAEnum, func, UUID, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import PlatformBase


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketCategory(str, enum.Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    FEATURE_REQUEST = "feature_request"
    BUG_REPORT = "bug_report"
    OTHER = "other"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class AuthorType(str, enum.Enum):
    """Disambiguates which database/side a message author belongs to."""
    HOSPITAL_USER = "hospital_user"
    PLATFORM_ADMIN = "platform_admin"


class SupportTicket(PlatformBase):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # The hospital user who opened the ticket (tenant DB UUID, no real FK)
    opened_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    ticket_number: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        # Format: TKT-00001
    )

    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[TicketCategory] = mapped_column(
        SAEnum(TicketCategory), nullable=False, default=TicketCategory.OTHER,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        SAEnum(TicketPriority), nullable=False, default=TicketPriority.NORMAL,
    )
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus), nullable=False, default=TicketStatus.OPEN, index=True,
    )

    # Platform admin currently assigned to this ticket (optional)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<SupportTicket id={self.id} number='{self.ticket_number}' status={self.status}>"


class TicketMessage(PlatformBase):
    __tablename__ = "ticket_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    author_type: Mapped[AuthorType] = mapped_column(SAEnum(AuthorType), nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<TicketMessage id={self.id} ticket_id={self.ticket_id} author_type={self.author_type}>"