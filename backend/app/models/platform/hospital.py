"""
Hospital Model (Platform Database)
------------------------------------
Represents a healthcare organization registered on MedNexus.
This model lives in the platform database — it is LATLOMP's record
of the hospital, not the hospital's own operational data.

Status lifecycle:
  pending_review → active → (suspended | deactivated)
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Boolean, DateTime, Enum as SAEnum,
    func, UUID
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import PlatformBase


class HospitalStatus(str, enum.Enum):
    """
    The status of a hospital account on the platform.
    
    pending_review: Application submitted, awaiting platform admin approval
    active:         Approved and operational
    suspended:      Temporarily disabled by platform admin (e.g., payment issue)
    deactivated:    Permanently disabled
    """
    PENDING_REVIEW = "pending_review"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class Hospital(PlatformBase):
    """
    Hospital table in the platform database.
    
    One row per registered hospital on the MedNexus platform.
    """
    __tablename__ = "hospitals"

    # ── Primary Key ───────────────────────────────────────────────────────────
    # We use UUID instead of auto-incrementing integers.
    # Why? UUIDs are globally unique across all systems.
    # This means hospital IDs can never collide, are unpredictable
    # (harder to enumerate), and work well in distributed systems.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Basic Information ─────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # slug: a URL-safe identifier used for routing (e.g., "city-general-hospital")
    # Must be unique across all hospitals
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    # Short description of the hospital
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # URL to the hospital's logo stored in Cloudflare R2
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Location ──────────────────────────────────────────────────────────────
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Contact ───────────────────────────────────────────────────────────────
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Verification ──────────────────────────────────────────────────────────
    # Government/regulatory registration numbers
    registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # URL to uploaded verification documents (license, certification, etc.)
    verification_documents_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Status & Approval ─────────────────────────────────────────────────────
    status: Mapped[HospitalStatus] = mapped_column(
        SAEnum(HospitalStatus),
        nullable=False,
        default=HospitalStatus.PENDING_REVIEW,
        index=True,  # Index because we filter by status frequently
    )

    # Who approved this hospital (platform admin ID)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Notes from the platform admin during review
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Suspension ────────────────────────────────────────────────────────────
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    # func.now() sets the value automatically at the database level
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
    # These allow us to access related records in Python:
    #   hospital.subscription  → the hospital's current subscription
    #   hospital.plan          → the hospital's current plan
    subscription: Mapped["Subscription"] = relationship(   # type: ignore # noqa: F821
        "Subscription",
        back_populates="hospital",
        uselist=False,  # One-to-one: a hospital has one subscription
    )

    def __repr__(self) -> str:
        return f"<Hospital id={self.id} name='{self.name}' status={self.status}>"