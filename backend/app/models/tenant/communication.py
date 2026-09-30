"""
Internal Communication Models (Tenant Database)
-------------------------------------------------------
Three models for staff-to-staff messaging within a single hospital:

  Conversation              → the thread (direct or department-scoped)
  ConversationParticipant   → many-to-many: which staff are in which thread
  Message                   → individual messages within a thread

Conversation Types:
  direct      → exactly 2 participants, created on first message between them
  department  → all active staff in a department at creation time; new
                hires joining later do NOT retroactively join existing
                department conversations (intentional — matches how
                Slack channels work: joining a channel is a separate
                action from the channel existing)
  group       → an arbitrary set of participants, for ad-hoc multi-person
                threads (e.g. "this patient's care team")

Read Tracking:
  ConversationParticipant.last_read_at is a single cursor timestamp,
  not a per-message read-receipt row. A message is unread for a
  participant if message.created_at > participant.last_read_at.
  This is the standard chat-app pattern (Slack, WhatsApp) — cheap to
  maintain regardless of conversation length, unlike a row-per-message
  read table which would grow unbounded.

Why no real FK from ConversationParticipant.user_id to HospitalUser:
  SQLAlchemy supports real FKs here since both tables live in the
  SAME tenant database (unlike the cross-database references seen
  elsewhere in this project for platform/tenant splits). We could
  add a real FK — deliberately omitted for consistency with every
  other "references a person" column in this codebase (booked_by,
  doctor_id, etc. are all plain UUID columns, validated in the
  service layer), not because it's impossible here.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Enum as SAEnum, func, UUID, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


class ConversationType(str, enum.Enum):
    DIRECT = "direct"
    DEPARTMENT = "department"
    GROUP = "group"


class Conversation(TenantBase):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    conversation_type: Mapped[ConversationType] = mapped_column(
        SAEnum(ConversationType), nullable=False, default=ConversationType.DIRECT,
    )

    # Display name — required for department/group, optional for direct
    # (direct conversations are usually displayed as "the other person's name"
    # by the frontend, computed from participants, not stored here)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # If conversation_type == department, which department this represents
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # Updated whenever a new message is posted — lets us sort conversation
    # lists by "most recently active" without joining against Message
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} type={self.conversation_type}>"


class ConversationParticipant(TenantBase):
    __tablename__ = "conversation_participants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # Cursor for unread-count calculation — see module docstring
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    # Soft-leave: participant left a group/department conversation without
    # deleting history for everyone else
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<ConversationParticipant conversation_id={self.conversation_id} user_id={self.user_id}>"


class Message(TenantBase):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Soft-delete: sender can retract a message; others see "message deleted"
    # rather than the row vanishing (preserves conversation flow/numbering)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} conversation_id={self.conversation_id} sender_id={self.sender_id}>"