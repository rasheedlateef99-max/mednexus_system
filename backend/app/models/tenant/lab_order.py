"""
Laboratory Order Models (Tenant Database)
--------------------------------------------
Two models representing a complete laboratory test order:

  LabOrder      → the order document (header)
  LabOrderItem  → individual tests requested (many per order)

Mirrors the Prescription/PrescriptionItem pattern from Milestone 8.

Lifecycle (LabOrder):
  ordered              → Doctor has placed the order
  sample_collected     → Specimen has been collected
  processing           → Lab is actively running tests
  completed            → All items have results
  partially_completed  → Some items completed, some pending/cancelled
  cancelled            → Order voided before completion

Lifecycle (LabOrderItem):
  pending    → Awaiting result entry
  completed  → Result has been entered
  cancelled  → This specific test was removed from the order

Abnormal / Critical Flagging:
  is_abnormal is set by the lab scientist when entering the result,
  or computed automatically if numeric_value falls outside the
  reference range bounds (when those bounds are provided numerically).
  is_critical flags results requiring immediate doctor notification
  (e.g., critically low potassium, critically high glucose).
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Boolean, DateTime,
    Enum as SAEnum, func, UUID, Numeric
)
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


class LabOrderStatus(str, enum.Enum):
    ORDERED = "ordered"
    SAMPLE_COLLECTED = "sample_collected"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    CANCELLED = "cancelled"


class LabOrderItemStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LabOrderPriority(str, enum.Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    STAT = "stat"   # Immediate — life-threatening situations


# ── Lab Order (Header) ────────────────────────────────────────────────────────

class LabOrder(TenantBase):
    """
    Laboratory order header.

    One row per order. Individual tests are stored in LabOrderItem rows.
    """
    __tablename__ = "lab_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    # Doctor who ordered the tests
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    medical_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )

    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    # Human-readable order number: LAB-00001
    order_number: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
    )

    # Clinical reason for ordering — helps the lab interpret results
    clinical_indication: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    priority: Mapped[LabOrderPriority] = mapped_column(
        SAEnum(LabOrderPriority),
        nullable=False,
        default=LabOrderPriority.ROUTINE,
        index=True,
    )

    status: Mapped[LabOrderStatus] = mapped_column(
        SAEnum(LabOrderStatus),
        nullable=False,
        default=LabOrderStatus.ORDERED,
        index=True,
    )

    # ── Specimen Collection ───────────────────────────────────────────────────
    specimen_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    specimen_collected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    specimen_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        # e.g. "Venous blood", "Urine", "Sputum", "Stool"
    )

    # ── Review / Sign-off ─────────────────────────────────────────────────────
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    doctor_comments: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    # ── Cancellation ──────────────────────────────────────────────────────────
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<LabOrder id={self.id} number='{self.order_number}' status={self.status}>"


# ── Lab Order Item (Individual Test) ──────────────────────────────────────────

class LabOrderItem(TenantBase):
    """
    Individual laboratory test within an order.

    One row per requested test per order.
    """
    __tablename__ = "lab_order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    # ── Test Information ──────────────────────────────────────────────────────
    test_name: Mapped[str] = mapped_column(
        String(255), nullable=False,
        # e.g. "Full Blood Count", "Liver Function Test", "Fasting Glucose"
    )
    test_category: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        # e.g. "Haematology", "Biochemistry", "Microbiology"
    )

    # ── Result ────────────────────────────────────────────────────────────────
    # Text result for non-numeric tests (e.g. "Negative", "No growth")
    result_value: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    # Numeric result for quantitative tests (e.g. 5.4, 120.0)
    # Stored separately from result_value to allow numeric comparison
    # against reference ranges and future trend charting
    numeric_value: Mapped[float | None] = mapped_column(
        Numeric(12, 4), nullable=True,
    )

    unit: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        # e.g. "mg/dL", "mmol/L", "x10^9/L"
    )

    reference_range: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        # e.g. "3.5 - 5.5 mmol/L", "Negative"
    )

    # Numeric bounds used for automatic abnormal detection (optional)
    reference_low: Mapped[float | None] = mapped_column(
        Numeric(12, 4), nullable=True,
    )
    reference_high: Mapped[float | None] = mapped_column(
        Numeric(12, 4), nullable=True,
    )

    is_abnormal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    is_critical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    # Lab scientist's interpretive notes
    lab_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    status: Mapped[LabOrderItemStatus] = mapped_column(
        SAEnum(LabOrderItemStatus),
        nullable=False,
        default=LabOrderItemStatus.PENDING,
        index=True,
    )

    # ── Result Entry Tracking ─────────────────────────────────────────────────
    resulted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    resulted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<LabOrderItem id={self.id} test='{self.test_name}' status={self.status}>"