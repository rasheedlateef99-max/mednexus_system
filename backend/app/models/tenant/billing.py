"""
Billing Models (Tenant Database)
------------------------------------
Three models representing the billing workflow:

  Invoice       → the billing document (header)
  InvoiceItem   → individual charge line items (many per invoice)
  Payment       → individual payment transactions (many per invoice)

Why Payment is separate from Invoice:
  Invoices are often paid in multiple installments. Each payment is
  a distinct event with its own date, method, and amount. Storing
  only a running total on the invoice would lose this history and
  make it impossible to reconcile "which payment covered what" or
  to process partial refunds against a specific transaction.

Invoice Status Lifecycle:
  draft           → Being built, not yet finalized
  issued          → Finalized, awaiting payment, due_date set
  partially_paid  → Some payment received, balance > 0
  paid            → balance_due == 0, fully settled
  overdue         → issued/partially_paid AND due_date has passed
  cancelled       → Voided before any payment
  refunded        → Fully refunded after being paid

Status is recomputed after every payment/refund event by the service
layer — it is never set directly by client request except for the
explicit cancel action.

Currency:
  Stored per-invoice (not assumed global) since MedNexus serves
  hospitals across multiple countries. No currency conversion logic
  exists yet — that is future scope tied to the Paystack/multi-provider
  payment integration milestone.
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


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class InvoiceItemType(str, enum.Enum):
    """Categorizes the source of a charge for reporting purposes."""
    CONSULTATION = "consultation"
    PROCEDURE = "procedure"
    PRESCRIPTION = "prescription"
    LABORATORY = "laboratory"
    ADMISSION = "admission"
    OTHER = "other"


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    MOBILE_MONEY = "mobile_money"
    INSURANCE = "insurance"
    OTHER = "other"


class PaymentType(str, enum.Enum):
    """Distinguishes a normal payment from a refund transaction."""
    PAYMENT = "payment"
    REFUND = "refund"


# ── Invoice (Header) ──────────────────────────────────────────────────────────

class Invoice(TenantBase):
    """
    Billing invoice header.

    One row per invoice. Charges live in InvoiceItem rows.
    Payments and refunds live in Payment rows.
    total_amount and balance_due are denormalized and maintained
    by the service layer after every line-item or payment change.
    """
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    # Optional traceability links — not enforced FKs at the model level,
    # validated in the service layer, same pattern as elsewhere in the system
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    prescription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    lab_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    invoice_number: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        # Format: INV-00001
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")

    # ── Denormalized totals (maintained by service layer) ────────────────────
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    amount_paid: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    balance_due: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    status: Mapped[InvoiceStatus] = mapped_column(
        SAEnum(InvoiceStatus), nullable=False, default=InvoiceStatus.DRAFT, index=True,
    )

    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Issuance ──────────────────────────────────────────────────────────────
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # ── Cancellation ──────────────────────────────────────────────────────────
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} number='{self.invoice_number}' status={self.status}>"


# ── Invoice Item (Line Item) ──────────────────────────────────────────────────

class InvoiceItem(TenantBase):
    """Individual charge line within an invoice."""
    __tablename__ = "invoice_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    item_type: Mapped[InvoiceItemType] = mapped_column(
        SAEnum(InvoiceItemType), nullable=False, default=InvoiceItemType.OTHER,
    )

    description: Mapped[str] = mapped_column(
        String(500), nullable=False,
        # e.g. "Consultation - Dr. Okafor", "Amoxicillin 500mg x21"
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False,
        # quantity * unit_price, computed and stored at creation time
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<InvoiceItem id={self.id} description='{self.description}' total={self.line_total}>"


# ── Payment ────────────────────────────────────────────────────────────────────

class Payment(TenantBase):
    """
    A single payment or refund transaction against an invoice.

    Multiple Payment rows can apply to one Invoice (partial payments
    over time). A refund is recorded as its own Payment row with
    payment_type='refund' rather than mutating an existing payment —
    this preserves a clean audit trail of every transaction.
    """
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )

    payment_type: Mapped[PaymentType] = mapped_column(
        SAEnum(PaymentType), nullable=False, default=PaymentType.PAYMENT,
    )

    amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False,
        # Always stored as a positive number; payment_type distinguishes
        # whether it adds to or subtracts from amount_paid
    )

    method: Mapped[PaymentMethod] = mapped_column(
        SAEnum(PaymentMethod), nullable=False, default=PaymentMethod.CASH,
    )

    reference_number: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        # Transaction reference, receipt number, bank reference, etc.
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    # For refunds: which original payment this refund relates to (optional)
    refund_of_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Payment id={self.id} invoice_id={self.invoice_id} amount={self.amount} type={self.payment_type}>"