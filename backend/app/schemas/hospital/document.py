"""
Document Generation Schemas
----------------------------------
Request/response shapes for PDF document generation and retrieval.
"""

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class GenerateInvoicePdfRequest(BaseModel):
    invoice_id: UUID


class GeneratePrescriptionPdfRequest(BaseModel):
    prescription_id: UUID


class GenerateLabReportPdfRequest(BaseModel):
    lab_order_id: UUID


class GeneratedDocumentResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    patient_id: UUID | None
    document_type: str
    source_type: str
    source_id: UUID
    storage_key: str
    file_size_bytes: int | None
    generated_by: UUID
    created_at: datetime

    download_url: str = Field(default="", description="Time-limited signed URL to fetch the PDF")

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    items: list[GeneratedDocumentResponse]
    total: int
    page: int
    per_page: int
    pages: int