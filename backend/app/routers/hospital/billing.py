"""
Billing Router
-----------------
HTTP endpoints for invoicing and payment management.

Access control:
  - Create/issue/cancel invoice, record payment: receptionist, accountant, admin
  - Refund: hospital admin only
  - View invoices, history, summary: all active staff

Route order: /summary and /patient-history before /{invoice_id}.

Endpoints:
  POST   /hospital/billing/invoices                          → Create draft invoice
  GET    /hospital/billing/invoices                            → List invoices
  GET    /hospital/billing/summary                             → Front-desk summary
  GET    /hospital/billing/patient-history/{pid}                → Patient billing history
  GET    /hospital/billing/invoices/{id}                        → Get invoice
  POST   /hospital/billing/invoices/{id}/issue                  → Issue draft invoice
  POST   /hospital/billing/invoices/{id}/payments                → Record payment
  POST   /hospital/billing/invoices/{id}/refund                  → Record refund
  POST   /hospital/billing/invoices/{id}/cancel                  → Cancel invoice
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user
from app.schemas.hospital.billing import (
    InvoiceCreateRequest,
    InvoiceIssueRequest,
    InvoiceCancelRequest,
    PaymentRecordRequest,
    RefundRequest,
    InvoiceResponse,
    InvoiceListResponse,
    PatientBillingHistoryResponse,
    BillingSummaryResponse,
)
from app.services.hospital.billing_service import (
    create_invoice,
    get_invoice,
    issue_invoice,
    record_payment,
    record_refund,
    cancel_invoice,
    list_invoices,
    get_patient_billing_history,
    get_billing_summary,
)

router = APIRouter()


@router.post(
    "/invoices",
    response_model=InvoiceResponse,
    status_code=201,
    summary="Create Draft Invoice",
    description="Creates a new draft invoice with line items. Receptionist, accountant, or admin only.",
)
async def create(
    data: InvoiceCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await create_invoice(db, ctx["hospital_id"], ctx["user"], data)


@router.get(
    "/summary",
    response_model=BillingSummaryResponse,
    summary="Billing Summary",
    description="Returns outstanding balances, overdue totals, and monthly revenue for the front-desk dashboard.",
)
async def summary(
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_billing_summary(db, ctx["hospital_id"])


@router.get(
    "/patient-history/{patient_id}",
    response_model=PatientBillingHistoryResponse,
    summary="Patient Billing History",
    description="Returns all invoices for a patient with total outstanding balance.",
)
async def patient_history(
    patient_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_patient_billing_history(db, ctx["hospital_id"], patient_id)


@router.get(
    "/invoices",
    response_model=InvoiceListResponse,
    summary="List Invoices",
    description="Returns a paginated list of invoices with optional patient and status filters.",
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    patient_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_invoices(db, ctx["hospital_id"], page, per_page, patient_id, status)


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceResponse,
    summary="Get Invoice",
    description="Returns full invoice detail including line items and payment history.",
)
async def get_one(
    invoice_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_invoice(db, ctx["hospital_id"], invoice_id)


@router.post(
    "/invoices/{invoice_id}/issue",
    response_model=InvoiceResponse,
    summary="Issue Invoice",
    description="Finalizes a draft invoice, locking totals and setting the due date.",
)
async def issue(
    invoice_id: UUID,
    data: InvoiceIssueRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await issue_invoice(db, ctx["hospital_id"], invoice_id, data, ctx["user"])


@router.post(
    "/invoices/{invoice_id}/payments",
    response_model=InvoiceResponse,
    summary="Record Payment",
    description="Records a full or partial payment against an issued invoice.",
)
async def pay(
    invoice_id: UUID,
    data: PaymentRecordRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await record_payment(db, ctx["hospital_id"], invoice_id, data, ctx["user"])


@router.post(
    "/invoices/{invoice_id}/refund",
    response_model=InvoiceResponse,
    summary="Record Refund",
    description="Records a refund against a paid invoice. Hospital admin only.",
)
async def refund(
    invoice_id: UUID,
    data: RefundRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await record_refund(db, ctx["hospital_id"], invoice_id, data, ctx["user"])


@router.post(
    "/invoices/{invoice_id}/cancel",
    response_model=InvoiceResponse,
    summary="Cancel Invoice",
    description="Cancels a draft or issued invoice with no payments recorded.",
)
async def cancel(
    invoice_id: UUID,
    data: InvoiceCancelRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await cancel_invoice(db, ctx["hospital_id"], invoice_id, data, ctx["user"])