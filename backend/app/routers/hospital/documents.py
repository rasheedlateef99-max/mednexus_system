"""
Document Generation Router
---------------------------------
Generates PDFs from existing invoices, prescriptions, and lab orders.
Resolves the real hospital name from the PLATFORM database (Hospital
model) before handing off to the tenant-side document service, since
GeneratedDocument and its source records live in the tenant DB but
the hospital's display name does not.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.database import get_tenant_db, get_platform_db
from app.core.dependencies import get_current_hospital_user
from app.models.platform.hospital import Hospital
from app.schemas.hospital.document import (
    GenerateInvoicePdfRequest, GeneratePrescriptionPdfRequest, GenerateLabReportPdfRequest,
    GeneratedDocumentResponse, DocumentListResponse,
)
from app.services.hospital.document_service import (
    generate_invoice_pdf, generate_prescription_pdf, generate_lab_report_pdf, list_documents,
)

router = APIRouter()


async def _resolve_hospital_name(platform_db: AsyncSession, hospital_id: UUID) -> str:
    result = await platform_db.execute(select(Hospital.name).where(Hospital.id == hospital_id))
    row = result.first()
    return row[0] if row else "Hospital"


@router.post(
    "/invoice",
    response_model=GeneratedDocumentResponse,
    status_code=201,
    summary="Generate Invoice PDF",
    description="Renders and uploads a PDF for an existing invoice. Returns a signed download URL.",
)
async def generate_invoice(
    data: GenerateInvoicePdfRequest,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    platform_db: AsyncSession = Depends(get_platform_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    hospital_name = await _resolve_hospital_name(platform_db, ctx["hospital_id"])
    return await generate_invoice_pdf(
        tenant_db, ctx["hospital_id"], data.invoice_id, ctx["user"].id, hospital_name,
    )


@router.post(
    "/prescription",
    response_model=GeneratedDocumentResponse,
    status_code=201,
    summary="Generate Prescription PDF",
    description="Renders and uploads a PDF for an existing prescription.",
)
async def generate_prescription(
    data: GeneratePrescriptionPdfRequest,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    platform_db: AsyncSession = Depends(get_platform_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    hospital_name = await _resolve_hospital_name(platform_db, ctx["hospital_id"])
    return await generate_prescription_pdf(
        tenant_db, ctx["hospital_id"], data.prescription_id, ctx["user"].id, hospital_name,
    )


@router.post(
    "/lab-report",
    response_model=GeneratedDocumentResponse,
    status_code=201,
    summary="Generate Lab Report PDF",
    description="Renders and uploads a PDF for an existing lab order.",
)
async def generate_lab_report(
    data: GenerateLabReportPdfRequest,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    platform_db: AsyncSession = Depends(get_platform_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    hospital_name = await _resolve_hospital_name(platform_db, ctx["hospital_id"])
    return await generate_lab_report_pdf(
        tenant_db, ctx["hospital_id"], data.lab_order_id, ctx["user"].id, hospital_name,
    )


@router.get(
    "/",
    response_model=DocumentListResponse,
    summary="List Generated Documents",
    description="Returns the document generation history for this hospital.",
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    patient_id: UUID | None = Query(default=None),
    document_type: str | None = Query(default=None),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_documents(tenant_db, ctx["hospital_id"], page, per_page, patient_id, document_type)