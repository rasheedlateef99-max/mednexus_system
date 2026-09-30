"""
Hospital Patient Router
-------------------------
HTTP endpoints for patient management.

All routes are scoped to the authenticated hospital via hospital_id
extracted from the JWT through get_current_hospital_user.

Access control per endpoint:
  - Register patient:    Any active hospital staff
  - View patient:        Any active hospital staff
  - Search patients:     Any active hospital staff
  - List patients:       Any active hospital staff
  - Update patient:      Any active hospital staff
  - Deactivate patient:  Hospital admin only
  - Patient stats:       Any active hospital staff

Endpoints:
  POST   /hospital/patients/              → Register new patient
  GET    /hospital/patients/              → List patients (paginated)
  GET    /hospital/patients/search        → Search patients
  GET    /hospital/patients/stats         → Patient statistics
  GET    /hospital/patients/{id}          → Get patient full profile
  GET    /hospital/patients/by-number/{n} → Get patient by PAT-XXXXX
  PUT    /hospital/patients/{id}          → Update patient record
  POST   /hospital/patients/{id}/deactivate → Deactivate patient
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user, require_clinical_staff
from app.schemas.hospital.patient import (
    PatientRegisterRequest,
    PatientUpdateRequest,
    PatientDetailResponse,
    PatientListResponse,
    PatientSearchResponse,
)
from app.services.hospital.patient_service import (
    register_patient,
    get_patient,
    get_patient_by_number,
    list_patients,
    search_patients,
    update_patient,
    deactivate_patient,
    get_patient_stats,
)

router = APIRouter()


@router.post(
    "/",
    response_model=PatientDetailResponse,
    status_code=201,
    summary="Register New Patient",
    description=(
        "Registers a new patient in the hospital. "
        "Only first_name and last_name are required. "
        "All other fields can be filled in progressively. "
        "Duplicate detection checks name + date of birth combination."
    ),
)
async def register(
    data: PatientRegisterRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """
    Register a new patient.

    Returns the full patient profile including the generated PAT-XXXXX number.
    If a potential duplicate is detected (same name + date of birth),
    returns a 409 Conflict with the existing patient's number.
    """
    return await register_patient(
        db=db,
        hospital_id=ctx["hospital_id"],
        registering_user=ctx["user"],
        data=data,
    )


@router.get(
    "/search",
    response_model=PatientSearchResponse,
    summary="Search Patients",
    description=(
        "Searches patients by name, phone number, email, patient number, "
        "or national ID. All searches are case-insensitive and partial. "
        "Returns matching patients ordered by last name."
    ),
)
async def search(
    q: str = Query(
        ...,
        min_length=1,
        description="Search term — name, phone, email, PAT number, or national ID",
    ),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """
    Search for patients.

    This endpoint must be defined BEFORE /{patient_id} in the router
    to prevent FastAPI from trying to interpret "search" as a patient UUID.
    """
    return await search_patients(
        db=db,
        hospital_id=ctx["hospital_id"],
        query_string=q,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/stats",
    summary="Patient Statistics",
    description=(
        "Returns summary statistics about the hospital's patient records. "
        "Used for dashboard overview panels."
    ),
)
async def stats(
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """Returns patient count statistics for the hospital dashboard."""
    data = await get_patient_stats(db, ctx["hospital_id"])
    return {"success": True, "data": data}


@router.get(
    "/by-number/{patient_number}",
    response_model=PatientDetailResponse,
    summary="Get Patient by Number",
    description=(
        "Fetches a patient using their human-readable PAT-XXXXX number. "
        "Useful when staff reference patients verbally or from printed records."
    ),
)
async def get_by_number(
    patient_number: str,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """Look up a patient by their PAT-XXXXX identifier."""
    return await get_patient_by_number(
        db=db,
        hospital_id=ctx["hospital_id"],
        patient_number=patient_number,
    )


@router.get(
    "/",
    response_model=PatientListResponse,
    summary="List Patients",
    description=(
        "Returns a paginated list of all active patients. "
        "Supports filtering by gender, blood type, and assigned doctor."
    ),
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    gender: str | None = Query(
        default=None,
        description="Filter by gender: male | female | other | prefer_not_to_say",
    ),
    blood_type: str | None = Query(
        default=None,
        description="Filter by blood type: A+ | A- | B+ | B- | AB+ | AB- | O+ | O-",
    ),
    assigned_doctor_id: UUID | None = Query(
        default=None,
        description="Filter by assigned doctor UUID",
    ),
    include_inactive: bool = Query(
        default=False,
        description="Include deactivated patient records",
    ),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """Returns paginated list of patients for this hospital."""
    return await list_patients(
        db=db,
        hospital_id=ctx["hospital_id"],
        page=page,
        per_page=per_page,
        gender_filter=gender,
        blood_type_filter=blood_type,
        assigned_doctor_id=assigned_doctor_id,
        include_inactive=include_inactive,
    )


@router.get(
    "/{patient_id}",
    response_model=PatientDetailResponse,
    summary="Get Patient Profile",
    description="Returns the complete profile for a single patient.",
)
async def get_one(
    patient_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """Fetch a patient's full profile by UUID."""
    return await get_patient(
        db=db,
        hospital_id=ctx["hospital_id"],
        patient_id=patient_id,
    )


@router.put(
    "/{patient_id}",
    response_model=PatientDetailResponse,
    summary="Update Patient Record",
    description=(
        "Updates a patient's information. "
        "Send only the fields you want to change — all fields are optional. "
        "Available to all active staff members."
    ),
)
async def update(
    patient_id: UUID,
    data: PatientUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """Update a patient record. Only provided fields are changed."""
    return await update_patient(
        db=db,
        hospital_id=ctx["hospital_id"],
        patient_id=patient_id,
        data=data,
    )


@router.post(
    "/{patient_id}/deactivate",
    summary="Deactivate Patient Record",
    description=(
        "Soft-deactivates a patient record. "
        "The record is preserved but hidden from normal queries. "
        "Requires hospital admin role."
    ),
)
async def deactivate(
    patient_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    """
    Deactivate a patient record (admin only).
    Record is preserved — only is_active is set to False.
    """
    result = await deactivate_patient(
        db=db,
        hospital_id=ctx["hospital_id"],
        patient_id=patient_id,
        requesting_user=ctx["user"],
    )
    return {"success": True, "data": result}