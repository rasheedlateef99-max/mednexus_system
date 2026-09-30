"""
Subscription Model (Platform Database)
----------------------------------------
Links a hospital to a plan and tracks the complete subscription lifecycle.

Status lifecycle:
  trial → active → (expired | cancelled)
  
A hospital always has exactly one subscription record.
Historical subscriptions are tracked via subscription_history (future).
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    String, Boolean, DateTime, Enum as SAEnum,
    ForeignKey, func, UUID, Numeric
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import PlatformBase


class SubscriptionStatus(str, enum.Enum):
    """
    The lifecycle status of a hospital subscription.
    
    trial:     Hospital is in the free trial period
    active:    Hospital has a paid, active subscription
    expired:   Subscription period ended without renewal
    cancelled: Hospital explicitly cancelled their subscription
    past_due:  Payment failed, grace period active
    """
    TRIAL = "trial"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PAST_DUE = "past_due"


class BillingCycle(str, enum.Enum):
    """Whether the hospital is billed monthly or annually."""
    MONTHLY = "monthly"
    ANNUAL = "annual"


class Subscription(PlatformBase):
    """
    Hospital subscription table.
    One row per hospital — tracks their current subscription state.
    """
    __tablename__ = "subscriptions"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Foreign Keys ──────────────────────────────────────────────────────────
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # One subscription per hospital
        index=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plans.id"),
        nullable=False,
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[SubscriptionStatus] = mapped_column(
        SAEnum(SubscriptionStatus),
        nullable=False,
        default=SubscriptionStatus.TRIAL,
        index=True,
    )

    billing_cycle: Mapped[BillingCycle] = mapped_column(
        SAEnum(BillingCycle),
        nullable=False,
        default=BillingCycle.MONTHLY,
    )

    # ── Trial Period ──────────────────────────────────────────────────────────
    trial_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # ── Billing Period ────────────────────────────────────────────────────────
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # ── Payment ───────────────────────────────────────────────────────────────
    # Stores the payment provider name for future multi-provider support
    payment_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # The subscription ID from the payment provider (e.g., Paystack's sub_xxx)
    external_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Price the hospital is actually paying (may differ from plan price due to discounts)
    amount_paid: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # ── Cancellation ──────────────────────────────────────────────────────────
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Auto Renewal ──────────────────────────────────────────────────────────
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    hospital: Mapped["Hospital"] = relationship(   # type: ignore # noqa: F821
        "Hospital",
        back_populates="subscription",
    )
    plan: Mapped["Plan"] = relationship(   # type: ignore # noqa: F821
        "Plan",
        back_populates="subscriptions",
    )

    def __repr__(self) -> str:
        return f"<Subscription hospital_id={self.hospital_id} status={self.status}>"