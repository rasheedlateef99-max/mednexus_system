"""
Plan Model (Platform Database)
--------------------------------
Represents a subscription plan offered by MedNexus.
Plans are created and managed by the LATLOMP platform owner.
Hospitals subscribe to plans during onboarding.

Examples: Free Trial, Starter, Professional, Enterprise
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, Numeric, DateTime, func, UUID, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import PlatformBase


class Plan(PlatformBase):
    """
    Subscription plan table.
    Defines the features and limits available at each pricing tier.
    """
    __tablename__ = "plans"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Basic Information ─────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    # Internal identifier used in code (e.g., "starter", "professional")
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Pricing ───────────────────────────────────────────────────────────────
    # Prices stored as Numeric to avoid floating-point precision issues with money
    price_monthly: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    price_annual: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    # ISO 4217 currency code (USD, NGN, GBP, etc.)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # ── Limits ────────────────────────────────────────────────────────────────
    # -1 means unlimited
    max_staff_users: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_patient_records: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    max_appointments_per_month: Mapped[int] = mapped_column(Integer, nullable=False, default=500)

    # ── Trial ─────────────────────────────────────────────────────────────────
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    has_trial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Feature Flags ─────────────────────────────────────────────────────────
    # JSON column allows flexible feature configuration without schema changes.
    # Example: {"lab_module": true, "pharmacy_module": true, "ai_assistant": false}
    features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # ── Visibility ────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Whether this plan is visible on the public pricing page
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Display order on pricing page (lower number = shown first)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

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
    subscriptions: Mapped[list["Subscription"]] = relationship(   # type: ignore # noqa: F821
        "Subscription",
        back_populates="plan",
    )

    def __repr__(self) -> str:
        return f"<Plan id={self.id} name='{self.name}' code='{self.code}'>"