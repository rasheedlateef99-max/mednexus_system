"""
Pharmacy Inventory Models (Tenant Database)
-----------------------------------------------
Two models representing pharmacy stock:

  PharmacyItem        → catalogue entry for a drug (one per drug per hospital)
  PharmacyStockBatch  → individual received batches (many per item)

Why batches are separate from the item:
  Each delivery of stock has its own expiry date, batch/lot number,
  and supplier. A single drug ("Amoxicillin 500mg") can have multiple
  batches in stock simultaneously, each expiring at a different time.
  Dispensing must deduct from the batch expiring soonest first (FEFO —
  First Expired, First Out), which is standard pharmacy practice.

PharmacyItem.total_quantity is a DENORMALIZED running total
  (sum of all non-expired, non-zero batch quantities). It is
  maintained by the service layer on every stock movement rather than
  computed with a live SUM() query on every read, because stock level
  is checked extremely frequently (on every dispense attempt) and
  needs to be fast.

Reorder Logic:
  reorder_level is the threshold below which the item is flagged
  as low stock. reorder_quantity is a suggested amount to reorder
  (informational only — no automatic purchase ordering in this version).
"""

import uuid
from datetime import datetime, date
from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, Date,
    Enum as SAEnum, func, UUID, Numeric
)
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


class StockMovementType(str, enum.Enum):
    """
    The kind of stock movement being recorded.
    Used in PharmacyStockBatch history / adjustments, and in the
    adjustment log entries.
    """
    RECEIVED = "received"          # New stock from supplier
    DISPENSED = "dispensed"        # Deducted via prescription dispensing
    ADJUSTED_UP = "adjusted_up"    # Manual correction — increase
    ADJUSTED_DOWN = "adjusted_down"  # Manual correction — decrease (wastage, loss, damage)
    EXPIRED_WRITTEN_OFF = "expired_written_off"  # Removed due to expiry


# ── Pharmacy Item (Catalogue Entry) ───────────────────────────────────────────

class PharmacyItem(TenantBase):
    """
    Catalogue entry for a drug stocked by the hospital pharmacy.

    One row per distinct drug+strength+form per hospital.
    Stock quantities live across PharmacyStockBatch rows; this row
    tracks the denormalized running total plus reorder configuration.
    """
    __tablename__ = "pharmacy_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    # ── Drug Identity ─────────────────────────────────────────────────────────
    drug_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    generic_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strength: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Classification ────────────────────────────────────────────────────────
    category: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        # e.g. "Antibiotic", "Analgesic", "Antihypertensive"
    )

    unit: Mapped[str] = mapped_column(
        String(50), nullable=False, default="units",
        # e.g. "tablets", "capsules", "ml", "vials"
    )

    # ── Stock Levels (denormalized) ──────────────────────────────────────────
    total_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        # Running total across all non-expired batches. Maintained by service.
    )

    reorder_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10,
        # When total_quantity falls to or below this, item is "low stock"
    )

    reorder_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50,
        # Suggested quantity to reorder (informational)
    )

    # ── Pricing (optional — useful for future billing integration) ──────────
    unit_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<PharmacyItem id={self.id} drug='{self.drug_name}' qty={self.total_quantity}>"


# ── Pharmacy Stock Batch ───────────────────────────────────────────────────────

class PharmacyStockBatch(TenantBase):
    """
    A single received batch of stock for a pharmacy item.

    Each batch has its own expiry date and quantity. Dispensing deducts
    from batches in expiry order (soonest first — FEFO).

    quantity_remaining reaches 0 when fully dispensed/adjusted out;
    the row is kept (not deleted) for audit history.
    """
    __tablename__ = "pharmacy_stock_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    pharmacy_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    batch_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        # Supplier's lot/batch number, if provided
    )

    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    quantity_received: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_remaining: Mapped[int] = mapped_column(Integer, nullable=False)

    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    received_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<PharmacyStockBatch id={self.id} "
            f"item_id={self.pharmacy_item_id} "
            f"remaining={self.quantity_remaining} "
            f"expires={self.expiry_date}>"
        )