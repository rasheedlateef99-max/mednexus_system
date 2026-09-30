"""
Billing Schemas
-----------------
Request and response shapes for invoicing and payment management.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime, date


# ── Invoice Item Schemas ──────────────────────────────────────────────────────

class InvoiceItemCreate(BaseModel):
    """A single charge line submitted when creating an invoice."""
    item_type: str = Field(
        default="other",
        description="consultation | procedure | prescription | laboratory | admission | other",
    )
    description: str = Field(..., min_length=1, max_length=500)
    quantity: int = Field(default=1, ge=1)
    unit_price: float = Field(..., ge=0)

    @field_validator("item_type")
    @classmethod
    def validate_type(cls, v):
        allowed = {"consultation", "procedure", "prescription", "laboratory", "admission", "other"}
        if v not in allowed:
            raise ValueError(f"item_type must be one of: {', '.join(sorted(allowed))}")
        return v


class InvoiceItemResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    invoice_id: UUID
    item_type: str
    description: str
    quantity: int
    unit_price: float
    line_total: float
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Invoice Schemas ────────────────────────────────────────────────────────────

class InvoiceCreateRequest(BaseModel):
    """
    Body for creating a new invoice (starts as draft).
    discount_amount and tax_amount are applied to the subtotal of items.
    """
    patient_id: UUID = Field(..., description="Patient being billed")
    appointment_id: UUID | None = None
    prescription_id: UUID | None = None
    lab_order_id: UUID | None = None
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    discount_amount: float = Field(default=0, ge=0)
    tax_amount: float = Field(default=0, ge=0)
    due_date: date | None = Field(None, description="Format: YYYY-MM-DD")
    notes: str | None = Field(None, max_length=1000)
    items: list[InvoiceItemCreate] = Field(
        ..., min_length=1, description="At least one charge required",
    )


class InvoiceIssueRequest(BaseModel):
    """Body for issuing (finalizing) a draft invoice."""
    due_date: date | None = Field(
        None,
        description="Overrides due_date if provided. Defaults to 14 days from issue if not set anywhere.",
    )

    @field_validator("due_date")
    @classmethod
    def validate_future(cls, v):
        if v is not None and v < date.today():
            raise ValueError("due_date cannot be in the past")
        return v


class InvoiceCancelRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class PaymentRecordRequest(BaseModel):
    """Body for recording a payment against an issued invoice."""
    amount: float = Field(..., gt=0)
    method: str = Field(
        default="cash",
        description="cash | card | bank_transfer | mobile_money | insurance | other",
    )
    reference_number: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=500)

    @field_validator("method")
    @classmethod
    def validate_method(cls, v):
        allowed = {"cash", "card", "bank_transfer", "mobile_money", "insurance", "other"}
        if v not in allowed:
            raise ValueError(f"method must be one of: {', '.join(sorted(allowed))}")
        return v


class RefundRequest(BaseModel):
    """
    Body for refunding a payment, in full or in part.
    A refund cannot exceed the total amount paid on the invoice.
    """
    amount: float = Field(..., gt=0)
    reason: str = Field(..., min_length=5, max_length=500)
    refund_of_payment_id: UUID | None = Field(
        None, description="Optionally tie this refund to a specific original payment",
    )


class PaymentResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    invoice_id: UUID
    payment_type: str
    amount: float
    method: str
    reference_number: str | None
    notes: str | None
    received_by: UUID | None
    refund_of_payment_id: UUID | None
    created_at: datetime

    received_by_name: str = ""

    model_config = {"from_attributes": True}


class InvoiceResponse(BaseModel):
    """Full invoice with items and payment history."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    appointment_id: UUID | None
    prescription_id: UUID | None
    lab_order_id: UUID | None
    invoice_number: str
    currency: str
    subtotal: float
    discount_amount: float
    tax_amount: float
    total_amount: float
    amount_paid: float
    balance_due: float
    status: str
    due_date: date | None
    notes: str | None
    issued_at: datetime | None
    issued_by: UUID | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime
    updated_at: datetime

    items: list[InvoiceItemResponse] = []
    payments: list[PaymentResponse] = []

    patient_name: str = ""

    model_config = {"from_attributes": True}


class InvoiceSummary(BaseModel):
    """Compact invoice for list views."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    invoice_number: str
    currency: str
    total_amount: float
    balance_due: float
    status: str
    due_date: date | None
    created_at: datetime

    patient_name: str = ""

    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    items: list[InvoiceSummary]
    total: int
    page: int
    per_page: int
    pages: int


class PatientBillingHistoryResponse(BaseModel):
    patient_id: UUID
    patient_name: str
    total_invoices: int
    total_outstanding_balance: float
    invoices: list[InvoiceSummary]


class BillingSummaryResponse(BaseModel):
    """Front-desk reporting snapshot."""
    total_outstanding: float
    total_overdue: float
    invoices_draft: int
    invoices_issued: int
    invoices_partially_paid: int
    invoices_overdue: int
    invoices_paid_this_month: int
    revenue_this_month: float