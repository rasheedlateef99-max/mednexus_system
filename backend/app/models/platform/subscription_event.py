"""
Subscription Event Model (Platform Database)
-------------------------------------------------
An append-only audit log of every subscription lifecycle event:
plan changes, payment attempts, payment confirmations, renewals,
cancellations, and expirations.

This is separate from the Subscription model itself (Milestone 3).
Subscription holds CURRENT STATE. SubscriptionEvent holds HISTORY.
Never updated after creation — only inserted.

Why this matters for billing:
  If a hospital disputes a charge or a webhook fires twice, this log
  is the source of truth for "what actually happened and when,"
  independent of whatever the current Subscription row says.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Enum as SAEnum, func, UUID, Numeric
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import PlatformBase


class SubscriptionEventType(str, enum.Enum):
    PLAN_SELECTED = "plan_selected"
    PAYMENT_INITIALIZED = "payment_initialized"
    PAYMENT_CONFIRMED = "payment_confirmed"
    PAYMENT_FAILED = "payment_failed"
    SUBSCRIPTION_ACTIVATED = "subscription_activated"
    SUBSCRIPTION_RENEWED = "subscription_renewed"
    SUBSCRIPTION_UPGRADED = "subscription_upgraded"
    SUBSCRIPTION_DOWNGRADED = "subscription_downgraded"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    TRIAL_STARTED = "trial_started"
    TRIAL_ENDED = "trial_ended"


class SubscriptionEvent(PlatformBase):
    __tablename__ = "subscription_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    event_type: Mapped[SubscriptionEventType] = mapped_column(
        SAEnum(SubscriptionEventType), nullable=False, index=True,
    )

    # Plan involved, if applicable
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Paystack transaction reference, for reconciliation
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Raw provider response, stored for debugging/audit (not parsed structure)
    provider_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<SubscriptionEvent id={self.id} type={self.event_type} hospital_id={self.hospital_id}>"