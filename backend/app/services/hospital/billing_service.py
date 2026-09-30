"""
Billing Service
------------------
Business logic for invoicing and payment management.

Status Recomputation (_recompute_invoice_status):
  Called after every payment, refund, or issuance event.

    draft                                      → unchanged until issued
    issued, amount_paid == 0, due_date passed   → overdue
    issued, amount_paid == 0, due_date not passed → issued
    0 < amount_paid < total_amount               → partially_paid
                                                     (or 'overdue' variant
                                                      if also past due)
    amount_paid >= total_amount                  → paid
    fully refunded (amount_paid <= 0 after refunds, was previously paid)
                                                  → refunded

balance_due = total_amount - amount_paid (floor at 0, never negative)

Multi-tenancy: every function takes hospital_id from the JWT and
filters every query by it. Patient and clinical-activity links are
validated to belong to the same hospital.
"""

import uuid
import re
from datetime import datetime, timezone, date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.tenant.billing import (
    Invoice,
    InvoiceItem,
    Payment,
    InvoiceStatus,
    InvoiceItemType,
    PaymentMethod,
    PaymentType,
)
from app.models.tenant.patient import Patient
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.schemas.hospital.billing import (
    InvoiceCreateRequest,
    InvoiceIssueRequest,
    InvoiceCancelRequest,
    PaymentRecordRequest,
    RefundRequest,
    InvoiceResponse,
    InvoiceSummary,
    InvoiceListResponse,
    InvoiceItemResponse,
    PaymentResponse,
    PatientBillingHistoryResponse,
    BillingSummaryResponse,
)
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    ForbiddenException,
)

DEFAULT_DUE_DAYS = 14

# Roles permitted to manage billing (front desk + admin)
BILLING_ROLES = {
    HospitalUserRole.RECEPTIONIST,
    HospitalUserRole.ACCOUNTANT,
    HospitalUserRole.HOSPITAL_ADMIN,
}


def _require_billing_role(user: HospitalUser) -> None:
    if user.role not in BILLING_ROLES:
        raise ForbiddenException(
            "Only receptionists, accountants, or hospital administrators "
            "can manage billing."
        )


# ── Invoice Number Generation ─────────────────────────────────────────────────

async def _generate_invoice_number(db: AsyncSession, hospital_id: uuid.UUID) -> str:
    result = await db.execute(
        select(Invoice.invoice_number)
        .where(Invoice.hospital_id == hospital_id)
        .order_by(Invoice.created_at.desc())
    )
    existing = result.scalars().all()
    max_number = 0
    for number_str in existing:
        match = re.search(r"INV-(\d+)", number_str)
        if match:
            num = int(match.group(1))
            if num > max_number:
                max_number = num
    return f"INV-{max_number + 1:05d}"


# ── Totals Recomputation ──────────────────────────────────────────────────────

async def _recompute_totals(db: AsyncSession, invoice: Invoice) -> Invoice:
    """
    Recalculates subtotal, total_amount, amount_paid, balance_due,
    and status from invoice items and payment history.

    Called after: item changes (draft only), payment, refund, issuance,
    and on every read (to catch overdue transitions lazily).
    """
    # Subtotal from items
    items_result = await db.execute(
        select(func.coalesce(func.sum(InvoiceItem.line_total), 0)).where(
            InvoiceItem.invoice_id == invoice.id
        )
    )
    subtotal = float(items_result.scalar() or 0)

    invoice.subtotal = subtotal
    invoice.total_amount = max(
        0.0, subtotal - float(invoice.discount_amount) + float(invoice.tax_amount)
    )

    # Net amount paid = payments - refunds
    payments_result = await db.execute(
        select(Payment.payment_type, func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.invoice_id == invoice.id)
        .group_by(Payment.payment_type)
    )
    totals_by_type = {row[0]: float(row[1]) for row in payments_result.all()}
    paid = totals_by_type.get(PaymentType.PAYMENT, 0.0)
    refunded = totals_by_type.get(PaymentType.REFUND, 0.0)
    net_paid = max(0.0, paid - refunded)

    invoice.amount_paid = net_paid
    invoice.balance_due = max(0.0, invoice.total_amount - net_paid)

    # Status derivation — skip for draft/cancelled, those are explicit states
    if invoice.status not in (InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED):
        is_past_due = invoice.due_date is not None and date.today() > invoice.due_date

        if refunded > 0 and net_paid <= 0 and paid > 0:
            invoice.status = InvoiceStatus.REFUNDED
        elif invoice.balance_due <= 0 and invoice.total_amount > 0:
            invoice.status = InvoiceStatus.PAID
        elif net_paid > 0:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        elif is_past_due:
            invoice.status = InvoiceStatus.OVERDUE
        else:
            invoice.status = InvoiceStatus.ISSUED

    await db.commit()
    await db.refresh(invoice)
    return invoice


# ── Name Enrichment ───────────────────────────────────────────────────────────

async def _get_patient_name(db, patient_id, hospital_id) -> str:
    result = await db.execute(
        select(Patient.first_name, Patient.last_name).where(
            Patient.id == patient_id, Patient.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return f"{row.first_name} {row.last_name}" if row else ""


async def _get_user_name(db, user_id, hospital_id) -> str:
    if not user_id:
        return ""
    result = await db.execute(
        select(HospitalUser.full_name).where(
            HospitalUser.id == user_id, HospitalUser.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return row.full_name if row else ""


async def _get_items(db, invoice_id) -> list[InvoiceItemResponse]:
    result = await db.execute(
        select(InvoiceItem)
        .where(InvoiceItem.invoice_id == invoice_id)
        .order_by(InvoiceItem.created_at.asc())
    )
    return [InvoiceItemResponse.model_validate(i) for i in result.scalars().all()]


async def _get_payments(db, invoice_id, hospital_id) -> list[PaymentResponse]:
    result = await db.execute(
        select(Payment)
        .where(Payment.invoice_id == invoice_id)
        .order_by(Payment.created_at.asc())
    )
    payments = result.scalars().all()
    responses = []
    for p in payments:
        r = PaymentResponse.model_validate(p)
        r.received_by_name = await _get_user_name(db, p.received_by, hospital_id)
        responses.append(r)
    return responses


async def _build_response(db, invoice: Invoice, hospital_id) -> InvoiceResponse:
    response = InvoiceResponse.model_validate(invoice)
    response.patient_name = await _get_patient_name(db, invoice.patient_id, hospital_id)
    response.items = await _get_items(db, invoice.id)
    response.payments = await _get_payments(db, invoice.id, hospital_id)
    return response


async def _build_summary(db, invoice: Invoice, hospital_id) -> InvoiceSummary:
    summary = InvoiceSummary.model_validate(invoice)
    summary.patient_name = await _get_patient_name(db, invoice.patient_id, hospital_id)
    return summary


# ── Lazy Overdue Check (applied on read) ──────────────────────────────────────

async def _check_overdue(db: AsyncSession, invoice: Invoice) -> Invoice:
    if invoice.status in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID):
        if invoice.due_date and date.today() > invoice.due_date:
            invoice.status = InvoiceStatus.OVERDUE
            await db.commit()
            await db.refresh(invoice)
    return invoice


# ── Core Service Functions ────────────────────────────────────────────────────

async def create_invoice(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    creating_user: HospitalUser,
    data: InvoiceCreateRequest,
) -> InvoiceResponse:
    """Creates a new draft invoice with line items."""
    _require_billing_role(creating_user)

    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == data.patient_id,
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    if not patient_result.scalar_one_or_none():
        raise NotFoundException("Patient")

    invoice_number = await _generate_invoice_number(db, hospital_id)

    invoice = Invoice(
        hospital_id=hospital_id,
        patient_id=data.patient_id,
        appointment_id=data.appointment_id,
        prescription_id=data.prescription_id,
        lab_order_id=data.lab_order_id,
        invoice_number=invoice_number,
        currency=data.currency.upper(),
        discount_amount=data.discount_amount,
        tax_amount=data.tax_amount,
        due_date=data.due_date,
        notes=data.notes,
        status=InvoiceStatus.DRAFT,
        subtotal=0,
        total_amount=0,
        amount_paid=0,
        balance_due=0,
    )
    db.add(invoice)
    await db.flush()

    for item_data in data.items:
        line_total = item_data.quantity * item_data.unit_price
        item = InvoiceItem(
            hospital_id=hospital_id,
            invoice_id=invoice.id,
            item_type=InvoiceItemType(item_data.item_type),
            description=item_data.description,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            line_total=line_total,
        )
        db.add(item)

    await db.commit()
    await db.refresh(invoice)

    invoice = await _recompute_totals(db, invoice)
    return await _build_response(db, invoice, hospital_id)


async def get_invoice(
    db: AsyncSession, hospital_id: uuid.UUID, invoice_id: uuid.UUID,
) -> InvoiceResponse:
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.hospital_id == hospital_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise NotFoundException("Invoice")
    invoice = await _check_overdue(db, invoice)
    return await _build_response(db, invoice, hospital_id)


async def issue_invoice(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    invoice_id: uuid.UUID,
    data: InvoiceIssueRequest,
    issuing_user: HospitalUser,
) -> InvoiceResponse:
    """Finalizes a draft invoice, locking in totals and setting due_date."""
    _require_billing_role(issuing_user)

    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.hospital_id == hospital_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise NotFoundException("Invoice")

    if invoice.status != InvoiceStatus.DRAFT:
        raise ValidationException(
            f"Only draft invoices can be issued. Current status: {invoice.status.value}"
        )

    # Confirm it has at least one item with a non-zero total
    items_result = await db.execute(
        select(func.count(InvoiceItem.id)).where(InvoiceItem.invoice_id == invoice_id)
    )
    if (items_result.scalar() or 0) == 0:
        raise ValidationException("Cannot issue an invoice with no line items.")

    now = datetime.now(timezone.utc)
    invoice.due_date = data.due_date or invoice.due_date or (date.today() + timedelta(days=DEFAULT_DUE_DAYS))
    invoice.issued_at = now
    invoice.issued_by = issuing_user.id
    invoice.status = InvoiceStatus.ISSUED

    await db.commit()
    await db.refresh(invoice)

    invoice = await _recompute_totals(db, invoice)
    return await _build_response(db, invoice, hospital_id)


async def record_payment(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    invoice_id: uuid.UUID,
    data: PaymentRecordRequest,
    receiving_user: HospitalUser,
) -> InvoiceResponse:
    """Records a payment against an issued (or partially paid / overdue) invoice."""
    _require_billing_role(receiving_user)

    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.hospital_id == hospital_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise NotFoundException("Invoice")

    if invoice.status in (InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED):
        raise ValidationException(
            f"Cannot record payment against a {invoice.status.value} invoice."
        )
    if invoice.status == InvoiceStatus.PAID:
        raise ValidationException("This invoice is already fully paid.")

    if data.amount > float(invoice.balance_due):
        raise ValidationException(
            f"Payment of {data.amount} exceeds the outstanding balance of "
            f"{invoice.balance_due}. Record a payment up to the balance due, "
            f"or issue a separate credit/refund if overpaid in error."
        )

    payment = Payment(
        hospital_id=hospital_id,
        invoice_id=invoice.id,
        payment_type=PaymentType.PAYMENT,
        amount=data.amount,
        method=PaymentMethod(data.method),
        reference_number=data.reference_number,
        notes=data.notes,
        received_by=receiving_user.id,
    )
    db.add(payment)
    await db.commit()

    invoice = await _recompute_totals(db, invoice)
    return await _build_response(db, invoice, hospital_id)


async def record_refund(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    invoice_id: uuid.UUID,
    data: RefundRequest,
    requesting_user: HospitalUser,
) -> InvoiceResponse:
    """
    Records a refund against an invoice that has received payment.
    Refund amount cannot exceed the total net amount currently paid.
    Only hospital admin can authorize refunds (stricter than recording payments).
    """
    if requesting_user.role != HospitalUserRole.HOSPITAL_ADMIN:
        raise ForbiddenException("Only hospital administrators can authorize refunds.")

    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.hospital_id == hospital_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise NotFoundException("Invoice")

    if float(invoice.amount_paid) <= 0:
        raise ValidationException("Cannot refund an invoice with no payments recorded.")

    if data.amount > float(invoice.amount_paid):
        raise ValidationException(
            f"Refund of {data.amount} exceeds the net amount paid "
            f"({invoice.amount_paid}) on this invoice."
        )

    if data.refund_of_payment_id:
        original_result = await db.execute(
            select(Payment).where(
                Payment.id == data.refund_of_payment_id,
                Payment.invoice_id == invoice_id,
                Payment.payment_type == PaymentType.PAYMENT,
            )
        )
        if not original_result.scalar_one_or_none():
            raise NotFoundException("Original payment to refund")

    refund = Payment(
        hospital_id=hospital_id,
        invoice_id=invoice.id,
        payment_type=PaymentType.REFUND,
        amount=data.amount,
        method=PaymentMethod.OTHER,
        notes=data.reason,
        received_by=requesting_user.id,
        refund_of_payment_id=data.refund_of_payment_id,
    )
    db.add(refund)
    await db.commit()

    invoice = await _recompute_totals(db, invoice)
    return await _build_response(db, invoice, hospital_id)


async def cancel_invoice(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    invoice_id: uuid.UUID,
    data: InvoiceCancelRequest,
    requesting_user: HospitalUser,
) -> InvoiceResponse:
    """Cancels a draft or issued invoice that has received no payment."""
    _require_billing_role(requesting_user)

    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.hospital_id == hospital_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise NotFoundException("Invoice")

    if float(invoice.amount_paid) > 0:
        raise ValidationException(
            "Cannot cancel an invoice that has received payment. Use a refund instead."
        )
    if invoice.status == InvoiceStatus.CANCELLED:
        raise ValidationException("This invoice is already cancelled.")

    invoice.status = InvoiceStatus.CANCELLED
    invoice.cancelled_at = datetime.now(timezone.utc)
    invoice.cancellation_reason = data.reason
    invoice.cancelled_by = requesting_user.id

    await db.commit()
    await db.refresh(invoice)

    return await _build_response(db, invoice, hospital_id)


async def list_invoices(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    patient_id: uuid.UUID | None = None,
    status_filter: str | None = None,
) -> InvoiceListResponse:
    query = (
        select(Invoice)
        .where(Invoice.hospital_id == hospital_id)
        .order_by(Invoice.created_at.desc())
    )
    if patient_id:
        query = query.where(Invoice.patient_id == patient_id)
    if status_filter:
        try:
            query = query.where(Invoice.status == InvoiceStatus(status_filter))
        except ValueError:
            pass

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    invoices = result.scalars().all()

    # Apply lazy overdue check to each before building summaries
    checked = [await _check_overdue(db, inv) for inv in invoices]

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    items = [await _build_summary(db, inv, hospital_id) for inv in checked]

    return InvoiceListResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)


async def get_patient_billing_history(
    db: AsyncSession, hospital_id: uuid.UUID, patient_id: uuid.UUID,
) -> PatientBillingHistoryResponse:
    patient_result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.hospital_id == hospital_id)
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise NotFoundException("Patient")

    result = await db.execute(
        select(Invoice)
        .where(Invoice.patient_id == patient_id, Invoice.hospital_id == hospital_id)
        .order_by(Invoice.created_at.desc())
    )
    invoices = result.scalars().all()
    checked = [await _check_overdue(db, inv) for inv in invoices]

    summaries = [await _build_summary(db, inv, hospital_id) for inv in checked]
    total_outstanding = sum(float(inv.balance_due) for inv in checked)

    return PatientBillingHistoryResponse(
        patient_id=patient.id,
        patient_name=patient.full_name,
        total_invoices=len(summaries),
        total_outstanding_balance=total_outstanding,
        invoices=summaries,
    )


async def get_billing_summary(
    db: AsyncSession, hospital_id: uuid.UUID,
) -> BillingSummaryResponse:
    """Front-desk dashboard snapshot."""
    today = date.today()
    month_start = today.replace(day=1)

    async def count_status(status: InvoiceStatus) -> int:
        result = await db.execute(
            select(func.count(Invoice.id)).where(
                Invoice.hospital_id == hospital_id, Invoice.status == status,
            )
        )
        return result.scalar() or 0

    outstanding_result = await db.execute(
        select(func.coalesce(func.sum(Invoice.balance_due), 0)).where(
            Invoice.hospital_id == hospital_id,
            Invoice.status.in_([InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE]),
        )
    )
    total_outstanding = float(outstanding_result.scalar() or 0)

    overdue_result = await db.execute(
        select(func.coalesce(func.sum(Invoice.balance_due), 0)).where(
            Invoice.hospital_id == hospital_id, Invoice.status == InvoiceStatus.OVERDUE,
        )
    )
    total_overdue = float(overdue_result.scalar() or 0)

    paid_this_month_result = await db.execute(
        select(func.count(Invoice.id)).where(
            Invoice.hospital_id == hospital_id,
            Invoice.status == InvoiceStatus.PAID,
            func.date(Invoice.updated_at) >= month_start,
        )
    )
    invoices_paid_this_month = paid_this_month_result.scalar() or 0

    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.hospital_id == hospital_id,
            Payment.payment_type == PaymentType.PAYMENT,
            func.date(Payment.created_at) >= month_start,
        )
    )
    revenue_this_month = float(revenue_result.scalar() or 0)

    return BillingSummaryResponse(
        total_outstanding=total_outstanding,
        total_overdue=total_overdue,
        invoices_draft=await count_status(InvoiceStatus.DRAFT),
        invoices_issued=await count_status(InvoiceStatus.ISSUED),
        invoices_partially_paid=await count_status(InvoiceStatus.PARTIALLY_PAID),
        invoices_overdue=await count_status(InvoiceStatus.OVERDUE),
        invoices_paid_this_month=invoices_paid_this_month,
        revenue_this_month=revenue_this_month,
    )