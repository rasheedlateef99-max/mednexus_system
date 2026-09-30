"""
Medical Records Router
------------------------
HTTP endpoints for clinical documentation.

Access control:
  - Create record:   Clinical staff only (doctor, nurse, hospital_admin)
  - Read record:     All active hospital staff
  - Update record:   Clinical staff only (draft records only)
  - Finalise record: Authoring doctor or hospital_admin
  - Amend record:    Clinical staff (within 24h window)
  - List records:    All active hospital staff
  - Patient history: All active hospital staff

Route order note:
  /stats, /patient-history, and other specific paths registered
  BEFORE /{record_id} to prevent path parameter collision.

Endpoints:
  POST   /hospital/medical-records/                          → Create record
  GET    /hospital/medical-records/                          → List records
  GET    /hospital/medical-records/patient-history/{pid}     → Patient history
  GET    /hospital/medical-records/{id}                      → Get record
  PUT    /hospital/medical-records/{id}                      → Update draft
  POST   /hospital/medical-records/{id}/finalise             → Finalise record
  POST   /hospital/medical-records/{id}/amend                → Amend record
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user, require_clinical_staff
from app.schemas.hospital.medical_record import (
    MedicalRecordCreateRequest,
    MedicalRecordUpdateRequest,
    MedicalRecordAmendRequest,
    MedicalRecordDetailResponse,
    MedicalRecordListResponse,
    PatientMedicalHistoryResponse,
)
from app.services.hospital.medical_record_service import (
    create_medical_record,
    get_medical_record,
    update_medical_record,
    finalise_medical_record,
    amend_medical_record,
    list_medical_records,
    get_patient_medical_history,
)

router = APIRouter()


@router.post(
    "/",
    response_model=MedicalRecordDetailResponse,
    status_code=201,
    summary="Create Medical Record",
    description=(
        "Creates a new medical record in draft status. "
        "Requires clinical staff role. "
        "If appointment_id is provided, enforces one-record-per-appointment rule."
    ),
)
async def create(
    data: MedicalRecordCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_clinical_staff),
):
    return await create_medical_record(
        db=db,
        hospital_id=ctx["hospital_id"],
        creating_doctor=ctx["user"],
        data=data,
    )


@router.get(
    "/patient-history/{patient_id}",
    response_model=PatientMedicalHistoryResponse,
    summary="Get Patient Medical History",
    description=(
        "Returns all medical records for a patient in chronological order. "
        "Most recent first. "
        "Used by doctors before consultations to review previous visits."
    ),
)
async def patient_history(
    patient_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """
    Complete medical history for one patient.
    All active hospital staff can view this.
    """
    return await get_patient_medical_history(
        db=db,
        hospital_id=ctx["hospital_id"],
        patient_id=patient_id,
    )


@router.get(
    "/",
    response_model=MedicalRecordListResponse,
    summary="List Medical Records",
    description=(
        "Returns a paginated list of medical records. "
        "Supports filtering by patient, doctor, status, diagnosis, and ICD-10 code."
    ),
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    patient_id: UUID | None = Query(default=None),
    doctor_id: UUID | None = Query(default=None),
    status: str | None = Query(
        default=None,
        description="draft | finalised | amended | locked",
    ),
    diagnosis: str | None = Query(
        default=None,
        description="Search by diagnosis text",
    ),
    icd10_code: str | None = Query(
        default=None,
        description="Filter by exact ICD-10 code e.g. J18.9",
    ),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_medical_records(
        db=db,
        hospital_id=ctx["hospital_id"],
        page=page,
        per_page=per_page,
        patient_id=patient_id,
        doctor_id=doctor_id,
        status_filter=status,
        diagnosis_search=diagnosis,
        icd10_filter=icd10_code,
    )


@router.get(
    "/{record_id}",
    response_model=MedicalRecordDetailResponse,
    summary="Get Medical Record",
    description=(
        "Returns the full details of a single medical record. "
        "Applies automatic locking if 24h amendment window has passed."
    ),
)
async def get_one(
    record_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_medical_record(db, ctx["hospital_id"], record_id)


@router.put(
    "/{record_id}",
    response_model=MedicalRecordDetailResponse,
    summary="Update Draft Record",
    description=(
        "Updates a draft medical record. "
        "Only works on records with status 'draft'. "
        "For post-finalisation edits, use the amend endpoint."
    ),
)
async def update(
    record_id: UUID,
    data: MedicalRecordUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_clinical_staff),
):
    return await update_medical_record(
        db=db,
        hospital_id=ctx["hospital_id"],
        record_id=record_id,
        data=data,
        requesting_user=ctx["user"],
    )


@router.post(
    "/{record_id}/finalise",
    response_model=MedicalRecordDetailResponse,
    summary="Finalise Medical Record",
    description=(
        "Finalises a draft medical record. "
        "Sets a 24-hour amendment window after which the record is locked. "
        "If linked to an appointment, automatically moves it to 'completed'. "
        "Only the authoring doctor or hospital admin can finalise."
    ),
)
async def finalise(
    record_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_clinical_staff),
):
    return await finalise_medical_record(
        db=db,
        hospital_id=ctx["hospital_id"],
        record_id=record_id,
        requesting_user=ctx["user"],
    )


@router.post(
    "/{record_id}/amend",
    response_model=MedicalRecordDetailResponse,
    summary="Amend Finalised Record",
    description=(
        "Amends a finalised or amended medical record. "
        "Only permitted within 24 hours of finalisation. "
        "Requires an explicit amendment reason which is logged permanently. "
        "Records all changed fields in the amendment audit trail."
    ),
)
async def amend(
    record_id: UUID,
    data: MedicalRecordAmendRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_clinical_staff),
):
    return await amend_medical_record(
        db=db,
        hospital_id=ctx["hospital_id"],
        record_id=record_id,
        data=data,
        requesting_user=ctx["user"],
    )