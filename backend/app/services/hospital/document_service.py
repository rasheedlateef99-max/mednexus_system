"""
Document Generation Service
-----------------------------------
Orchestrates: fetch source data → render PDF → upload to R2 → log
in GeneratedDocument.

Each generate_* function follows the same three-step pattern and is
intentionally NOT a generic "generate(document_type, source_id)"
dispatcher — each document type pulls different related data (an
invoice needs items+payments, a prescription needs items+doctor name,
a lab report needs items+doctor name+comments), so explicit functions
keep each one readable rather than building a configuration-driven
abstraction for three cases.
"""

import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.tenant.generated_document import GeneratedDocument, DocumentType
from app.models.tenant.billing import Invoice, InvoiceItem, Payment
from app.models.tenant.prescription import Prescription, PrescriptionItem
from app.models.tenant.lab_order import LabOrder, LabOrderItem
from app.models.tenant.patient import Patient
from app.models.tenant.hospital_user import HospitalUser
from app.services.hospital.pdf_renderer import (
    render_invoice_pdf, render_prescription_pdf, render_lab_report_pdf,
)
from app.services.hospital.storage_service import upload_pdf, build_storage_key, get_signed_download_url
from app.schemas.hospital.document import GeneratedDocumentResponse
from app.core.exceptions import NotFoundException


async def _get_hospital_name(db: AsyncSession, hospital_id: uuid.UUID) -> str:
    # Hospital lives in the PLATFORM db; this is a tenant-side service,
    # so for now we accept hospital_name as a simple placeholder pulled
    # from settings/context rather than a cross-database call here —
    # the router layer passes it in from the authenticated context.
    return "Hospital"  # overridden by caller where available


async def _build_doc_response(db: AsyncSession, doc: GeneratedDocument) -> GeneratedDocumentResponse:
    response = GeneratedDocumentResponse.model_validate(doc)
    response.download_url = get_signed_download_url(doc.storage_key)
    return response


async def generate_invoice_pdf(
    db: AsyncSession, hospital_id: uuid.UUID, invoice_id: uuid.UUID,
    generating_user_id: uuid.UUID, hospital_name: str,
) -> GeneratedDocumentResponse:
    inv_result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.hospital_id == hospital_id)
    )
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise NotFoundException("Invoice")

    items_result = await db.execute(select(InvoiceItem).where(InvoiceItem.invoice_id == invoice_id))
    items = items_result.scalars().all()

    payments_result = await db.execute(select(Payment).where(Payment.invoice_id == invoice_id))
    payments = payments_result.scalars().all()

    patient_result = await db.execute(select(Patient).where(Patient.id == invoice.patient_id))
    patient = patient_result.scalar_one_or_none()
    patient_name = patient.full_name if patient else ""

    pdf_bytes = render_invoice_pdf(invoice, items, payments, hospital_name, patient_name)

    storage_key = build_storage_key(str(hospital_id), "invoice", str(invoice.id))
    file_size = upload_pdf(storage_key, pdf_bytes)

    doc = GeneratedDocument(
        hospital_id=hospital_id,
        patient_id=invoice.patient_id,
        document_type=DocumentType.INVOICE,
        source_type="invoice",
        source_id=invoice.id,
        storage_key=storage_key,
        file_size_bytes=file_size,
        generated_by=generating_user_id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return await _build_doc_response(db, doc)


async def generate_prescription_pdf(
    db: AsyncSession, hospital_id: uuid.UUID, prescription_id: uuid.UUID,
    generating_user_id: uuid.UUID, hospital_name: str,
) -> GeneratedDocumentResponse:
    rx_result = await db.execute(
        select(Prescription).where(Prescription.id == prescription_id, Prescription.hospital_id == hospital_id)
    )
    prescription = rx_result.scalar_one_or_none()
    if not prescription:
        raise NotFoundException("Prescription")

    items_result = await db.execute(
        select(PrescriptionItem).where(PrescriptionItem.prescription_id == prescription_id)
    )
    items = items_result.scalars().all()

    patient_result = await db.execute(select(Patient).where(Patient.id == prescription.patient_id))
    patient = patient_result.scalar_one_or_none()
    patient_name = patient.full_name if patient else ""

    doctor_result = await db.execute(select(HospitalUser).where(HospitalUser.id == prescription.doctor_id))
    doctor = doctor_result.scalar_one_or_none()
    doctor_name = doctor.full_name if doctor else ""

    pdf_bytes = render_prescription_pdf(prescription, items, hospital_name, patient_name, doctor_name)

    storage_key = build_storage_key(str(hospital_id), "prescription", str(prescription.id))
    file_size = upload_pdf(storage_key, pdf_bytes)

    doc = GeneratedDocument(
        hospital_id=hospital_id,
        patient_id=prescription.patient_id,
        document_type=DocumentType.PRESCRIPTION,
        source_type="prescription",
        source_id=prescription.id,
        storage_key=storage_key,
        file_size_bytes=file_size,
        generated_by=generating_user_id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return await _build_doc_response(db, doc)


async def generate_lab_report_pdf(
    db: AsyncSession, hospital_id: uuid.UUID, lab_order_id: uuid.UUID,
    generating_user_id: uuid.UUID, hospital_name: str,
) -> GeneratedDocumentResponse:
    order_result = await db.execute(
        select(LabOrder).where(LabOrder.id == lab_order_id, LabOrder.hospital_id == hospital_id)
    )
    lab_order = order_result.scalar_one_or_none()
    if not lab_order:
        raise NotFoundException("Lab order")

    items_result = await db.execute(select(LabOrderItem).where(LabOrderItem.lab_order_id == lab_order_id))
    items = items_result.scalars().all()

    patient_result = await db.execute(select(Patient).where(Patient.id == lab_order.patient_id))
    patient = patient_result.scalar_one_or_none()
    patient_name = patient.full_name if patient else ""

    doctor_result = await db.execute(select(HospitalUser).where(HospitalUser.id == lab_order.doctor_id))
    doctor = doctor_result.scalar_one_or_none()
    doctor_name = doctor.full_name if doctor else ""

    pdf_bytes = render_lab_report_pdf(lab_order, items, hospital_name, patient_name, doctor_name)

    storage_key = build_storage_key(str(hospital_id), "lab_report", str(lab_order.id))
    file_size = upload_pdf(storage_key, pdf_bytes)

    doc = GeneratedDocument(
        hospital_id=hospital_id,
        patient_id=lab_order.patient_id,
        document_type=DocumentType.LAB_REPORT,
        source_type="lab_order",
        source_id=lab_order.id,
        storage_key=storage_key,
        file_size_bytes=file_size,
        generated_by=generating_user_id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return await _build_doc_response(db, doc)


async def list_documents(
    db: AsyncSession, hospital_id: uuid.UUID, page: int = 1, per_page: int = 20,
    patient_id: uuid.UUID | None = None, document_type: str | None = None,
):
    from app.schemas.hospital.document import DocumentListResponse
    from sqlalchemy import func

    query = select(GeneratedDocument).where(GeneratedDocument.hospital_id == hospital_id).order_by(
        GeneratedDocument.created_at.desc()
    )
    if patient_id:
        query = query.where(GeneratedDocument.patient_id == patient_id)
    if document_type:
        try:
            query = query.where(GeneratedDocument.document_type == DocumentType(document_type))
        except ValueError:
            pass

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    docs = result.scalars().all()

    items = [await _build_doc_response(db, d) for d in docs]
    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return DocumentListResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)