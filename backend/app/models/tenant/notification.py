"""
Notification Model (Tenant Database)
-----------------------------------------
One row per (event, recipient_user). Created by service hooks at the
point events occur; read by the recipient's notification list endpoint.

Design notes:
  is_read   → toggled by the recipient via the mark-read endpoint
  link_path → a relative frontend path for deep-linking, e.g.
               "/lab-orders/{id}" or "/support-tickets/{id}".
               Stored as a string; the frontend resolves it.
               Never an absolute URL — avoids hardcoding domain.

  hospital_id is stored for scoping (all tenant queries scope by
  hospital_id) but the notification itself belongs to a specific
  user (recipient_id). Both are needed.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Boolean, Enum as SAEnum, func, UUID
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


class NotificationEventType(str, enum.Enum):
    CRITICAL_LAB_RESULT = "critical_lab_result"
    SUPPORT_TICKET_REPLY = "support_ticket_reply"
    SUPPORT_TICKET_RESOLVED = "support_ticket_resolved"
    SUPPORT_TICKET_CLOSED = "support_ticket_closed"
    ANNOUNCEMENT_PUBLISHED = "announcement_published"
    PRESCRIPTION_DISPENSED = "prescription_dispensed"


class Notification(TenantBase):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    recipient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    event_type: Mapped[NotificationEventType] = mapped_column(
        SAEnum(NotificationEventType), nullable=False, index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Relative frontend path for deep-linking — e.g. "/lab-orders/{uuid}"
    link_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    # The source entity this notification is about (for deduplication/lookup)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    def __repr__(self) -> str:
        return f"<Notification id={self.id} type={self.event_type} recipient={self.recipient_id} read={self.is_read}>"