"""
Prescription Models (Tenant Database)
----------------------------------------
Two models representing a complete digital prescription:

  Prescription      → the prescription document (header)
  PrescriptionItem  → individual drug lines (many per prescription)

Relationship:
  One Prescription has many PrescriptionItems.
  A prescription without items is valid (items added progressively).
  A prescription with all items dispensed becomes 'dispensed'.

Design Decisions:
  - drug_name is free text (no drug database required at this stage)
  - dosage, frequency, duration are free text for clinical flexibility
  - quantity is numeric for pharmacy inventory integration (future)
  - dispensed_quantity tracks partial dispensing
  - validity_days controls prescription expiry

Lifecycle (Prescription):
  issued              → Written by doctor, awaiting pharmacy
  dispensing          → Pharmacist has started processing
  dispensed           → All items fully dispensed to patient
  partially_dispensed → Some items dispensed (stock shortage etc.)
  cancelled           → Voided before dispensing
  expired             → Validity period passed without dispensing

Lifecycle (PrescriptionItem):
  pending    → Awaiting dispensing
  dispensed  → This specific item has been dispensed
  not_available → Out of stock or unavailable
  cancelled  → This item was removed from the prescription
"""

import uuid
from datetime import datetime, date
from sqlalchemy import (
    String, Text, Integer, Numeric, Boolean,
    DateTime, Date, Enum as SAEnum, func, UUID
)
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


# ── Prescription Status ───────────────────────────────────────────────────────

class PrescriptionStatus(str, enum.Enum):
    ISSUED = "issued"
    DISPENSING = "dispensing"
    DISPENSED = "dispensed"
    PARTIALLY_DISPENSED = "partially_dispensed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# ── Prescription Item Status ──────────────────────────────────────────────────

class PrescriptionItemStatus(str, enum.Enum):
    PENDING = "pending"
    DISPENSED = "dispensed"
    NOT_AVAILABLE = "not_available"
    CANCELLED = "cancelled"


# ── Prescription (Header) ─────────────────────────────────────────────────────

class Prescription(TenantBase):
    """
    Prescription document header.

    One row per prescription. Contains metadata about the prescription:
    who issued it, for which patient, linked to which record/appointment,
    and the overall dispensing status.

    Individual drugs are stored in PrescriptionItem rows.
    """
    __tablename__ = "prescriptions"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Tenant Key ────────────────────────────────────────────────────────────
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # ── Core References ───────────────────────────────────────────────────────
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Doctor who issued the prescription
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Optional link to the medical record this prescription came from
    medical_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # Optional link to the appointment
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # ── Prescription Details ──────────────────────────────────────────────────

    # Human-readable prescription number within this hospital
    # Format: RX-00001, RX-00002 etc.
    prescription_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    # Clinical notes for the pharmacist
    pharmacist_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Diagnosis or indication for prescribing (optional but useful)
    diagnosis_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    # ── Validity ──────────────────────────────────────────────────────────────
    # How many days this prescription is valid for
    validity_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )

    # Computed from created_at + validity_days
    # Stored for efficient querying of expired prescriptions
    expires_on: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[PrescriptionStatus] = mapped_column(
        SAEnum(PrescriptionStatus),
        nullable=False,
        default=PrescriptionStatus.ISSUED,
        index=True,
    )

    # ── Dispensing Tracking ───────────────────────────────────────────────────
    # Pharmacist who processed this prescription
    dispensed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    dispensed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Cancellation ─────────────────────────────────────────────────────────
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

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

    def __repr__(self) -> str:
        return (
            f"<Prescription id={self.id} "
            f"number='{self.prescription_number}' "
            f"status={self.status}>"
        )


# ── Prescription Item (Line Item) ─────────────────────────────────────────────

class PrescriptionItem(TenantBase):
    """
    Individual drug line within a prescription.

    One row per drug per prescription.
    Tracks its own dispensing status independently of other items.

    Why free text for drug_name, dosage, frequency:
      Building a drug database is a significant project on its own.
      Free text allows any drug to be prescribed immediately.
      A structured drug database (with interaction checking) is a
      Phase 6 AI feature.
    """
    __tablename__ = "prescription_items"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Tenant Key ────────────────────────────────────────────────────────────
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # ── Parent Reference ──────────────────────────────────────────────────────
    prescription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # ── Drug Information ──────────────────────────────────────────────────────

    # Drug name — free text (e.g. "Amoxicillin", "Paracetamol 500mg tabs")
    drug_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Generic name if brand name used above
    generic_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Strength/concentration (e.g. "500mg", "250mg/5ml")
    strength: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Dosage form (e.g. "tablet", "capsule", "syrup", "injection")
    dosage_form: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Dosing Instructions ───────────────────────────────────────────────────

    # Dose per administration (e.g. "1 tablet", "10ml", "500mg")
    dose: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # How often (e.g. "Once daily", "Twice daily", "TDS", "QDS", "PRN")
    frequency: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # How long to take (e.g. "7 days", "2 weeks", "Until finished")
    duration: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Route of administration (e.g. "Oral", "Topical", "IV", "IM")
    route: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Additional instructions (e.g. "Take with food", "Avoid alcohol")
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Quantity ──────────────────────────────────────────────────────────────

    # Total quantity prescribed (e.g. 21 tablets for TDS x7 days)
    quantity_prescribed: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # Quantity unit (e.g. "tablets", "capsules", "ml", "vials")
    quantity_unit: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Dispensing ────────────────────────────────────────────────────────────

    # How much was actually dispensed (may differ if partial stock)
    quantity_dispensed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    status: Mapped[PrescriptionItemStatus] = mapped_column(
        SAEnum(PrescriptionItemStatus),
        nullable=False,
        default=PrescriptionItemStatus.PENDING,
        index=True,
    )

    # Pharmacist notes on this specific item
    dispensing_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Substitution: was a generic substituted for a brand?
    substituted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # If substituted, what was dispensed instead
    substitution_drug: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

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

    def __repr__(self) -> str:
        return (
            f"<PrescriptionItem id={self.id} "
            f"drug='{self.drug_name}' "
            f"status={self.status}>"
        )