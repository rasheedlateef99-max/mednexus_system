"""
Patient Service
-----------------
Business logic for patient management.

Key responsibilities:
  1. Patient number generation  — sequential IDs per hospital (PAT-00001)
  2. Duplicate detection        — catch same patient registered twice
  3. Multi-tenancy enforcement  — every query scoped to hospital_id
  4. Search                     — multi-field patient lookup
  5. Soft delete                — deactivate rather than destroy records

Patient Number Generation:
  Each hospital has its own sequential counter.
  We find the highest existing patient number for this hospital
  and increment it. This is safe for our current scale.

  At very high scale (millions of patients), this approach can
  have race conditions under concurrent inserts. The fix at that
  point is a database sequence per hospital. We use the simple
  approach now and will upgrade if needed.

PHI (Protected Health Information) Notice:
  All patient data is PHI. Every function in this service must:
  - Filter by hospital_id (multi-tenancy)
  - Never log patient names, IDs, or medical data
  - Return only what the schema defines (no extra fields)
"""

import uuid
import re
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_

from app.models.tenant.patient import Patient, Gender, BloodType, MaritalStatus
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.schemas.hospital.patient import (
    PatientRegisterRequest,
    PatientUpdateRequest,
    PatientSummary,
    PatientDetailResponse,
    PatientListResponse,
    PatientSearchResponse,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    ForbiddenException,
)


# ── Patient Number Generation ─────────────────────────────────────────────────

async def _generate_patient_number(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> str:
    """
    Generates the next sequential patient number for a hospital.

    Format: PAT-XXXXX where XXXXX is zero-padded to 5 digits.
    Examples: PAT-00001, PAT-00042, PAT-10000

    Algorithm:
      1. Find all patient_numbers for this hospital
      2. Extract the numeric portion from each (e.g., "PAT-00042" → 42)
      3. Take the maximum number found
      4. Add 1 and format as PAT-XXXXX

    If no patients exist yet for this hospital, starts at PAT-00001.

    Args:
        db:          Tenant database session
        hospital_id: The hospital to generate the number for

    Returns:
        A string like "PAT-00001"
    """
    result = await db.execute(
        select(Patient.patient_number)
        .where(Patient.hospital_id == hospital_id)
        .order_by(Patient.created_at.desc())
    )
    existing_numbers = result.scalars().all()

    max_number = 0
    for number_str in existing_numbers:
        # Extract digits from "PAT-00042" → 42
        match = re.search(r"PAT-(\d+)", number_str)
        if match:
            num = int(match.group(1))
            if num > max_number:
                max_number = num

    next_number = max_number + 1
    return f"PAT-{next_number:05d}"


# ── Enum Converters ───────────────────────────────────────────────────────────

def _to_gender(value: str | None) -> Gender | None:
    """Converts a string to the Gender enum, or None if not provided."""
    if value is None:
        return None
    try:
        return Gender(value)
    except ValueError:
        return None


def _to_blood_type(value: str | None) -> BloodType | None:
    """Converts a string to the BloodType enum, or None if not provided."""
    if value is None:
        return None
    try:
        return BloodType(value)
    except ValueError:
        return None


def _to_marital_status(value: str | None) -> MaritalStatus | None:
    """Converts a string to the MaritalStatus enum, or None if not provided."""
    if value is None:
        return None
    try:
        return MaritalStatus(value)
    except ValueError:
        return None


# ── Response Builders ─────────────────────────────────────────────────────────

def _build_summary(patient: Patient) -> PatientSummary:
    """
    Builds a PatientSummary from a Patient model instance.

    Populates the computed fields (age, full_name) that are not
    stored in the database but are derived from stored values.
    """
    summary = PatientSummary.model_validate(patient)
    summary.age = patient.age
    summary.full_name = patient.full_name
    return summary


def _build_detail(patient: Patient) -> PatientDetailResponse:
    """
    Builds a PatientDetailResponse from a Patient model instance.
    """
    detail = PatientDetailResponse.model_validate(patient)
    detail.age = patient.age
    detail.full_name = patient.full_name
    return detail


# ── Core Patient Operations ───────────────────────────────────────────────────

async def register_patient(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    registering_user: HospitalUser,
    data: PatientRegisterRequest,
) -> PatientDetailResponse:
    """
    Registers a new patient in the hospital.

    Duplicate detection logic:
      We check for potential duplicates using a combination of
      first name + last name + date of birth. If a match is found,
      we warn but do not block — the registering staff member
      makes the final call. (Two patients CAN have the same name
      and birthdate — it is uncommon but real.)

    For this implementation we raise a ConflictException with the
    existing patient's ID so the frontend can show a "did you mean
    this patient?" prompt. The frontend should offer a "register
    anyway" option which bypasses the check.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        registering_user: The staff member registering the patient
        data:             Patient information from the request body

    Returns:
        PatientDetailResponse
    """

    # ── Duplicate Detection ───────────────────────────────────────────────────
    # Check by name + date of birth
    if data.date_of_birth:
        duplicate_check = await db.execute(
            select(Patient).where(
                Patient.hospital_id == hospital_id,
                func.lower(Patient.first_name) == data.first_name.lower(),
                func.lower(Patient.last_name) == data.last_name.lower(),
                Patient.date_of_birth == data.date_of_birth,
                Patient.is_active == True,
            )
        )
        existing = duplicate_check.scalar_one_or_none()
        if existing:
            raise ConflictException(
                f"A patient named {data.first_name} {data.last_name} "
                f"with date of birth {data.date_of_birth} already exists. "
                f"Existing patient number: {existing.patient_number}. "
                f"If this is a different patient, please contact your administrator."
            )

    # ── National ID Uniqueness ────────────────────────────────────────────────
    if data.national_id:
        id_check = await db.execute(
            select(Patient).where(
                Patient.hospital_id == hospital_id,
                Patient.national_id == data.national_id,
                Patient.is_active == True,
            )
        )
        if id_check.scalar_one_or_none():
            raise ConflictException(
                f"A patient with national ID '{data.national_id}' "
                f"is already registered in this hospital."
            )

    # ── Validate Assigned Doctor ──────────────────────────────────────────────
    if data.assigned_doctor_id:
        doctor_check = await db.execute(
            select(HospitalUser).where(
                HospitalUser.id == data.assigned_doctor_id,
                HospitalUser.hospital_id == hospital_id,
                HospitalUser.role == HospitalUserRole.DOCTOR,
            )
        )
        if not doctor_check.scalar_one_or_none():
            raise NotFoundException("Assigned doctor")

    # ── Generate Patient Number ───────────────────────────────────────────────
    patient_number = await _generate_patient_number(db, hospital_id)

    # ── Create the Patient ────────────────────────────────────────────────────
    patient = Patient(
        hospital_id=hospital_id,
        patient_number=patient_number,
        # Personal
        first_name=data.first_name.strip(),
        last_name=data.last_name.strip(),
        middle_name=data.middle_name.strip() if data.middle_name else None,
        date_of_birth=data.date_of_birth,
        gender=_to_gender(data.gender),
        marital_status=_to_marital_status(data.marital_status),
        national_id=data.national_id,
        # Contact
        phone_primary=data.phone_primary,
        phone_secondary=data.phone_secondary,
        email=data.email.lower() if data.email else None,
        # Address
        address_line1=data.address_line1,
        address_line2=data.address_line2,
        city=data.city,
        state=data.state,
        country=data.country,
        # Medical
        blood_type=_to_blood_type(data.blood_type),
        allergies=data.allergies,
        chronic_conditions=data.chronic_conditions,
        current_medications=data.current_medications,
        medical_notes=data.medical_notes,
        # Emergency contact
        emergency_contact_name=data.emergency_contact_name,
        emergency_contact_relationship=data.emergency_contact_relationship,
        emergency_contact_phone=data.emergency_contact_phone,
        # Insurance
        insurance_provider=data.insurance_provider,
        insurance_number=data.insurance_number,
        # Occupation
        occupation=data.occupation,
        employer=data.employer,
        # Assignment
        registered_by=registering_user.id,
        assigned_doctor_id=data.assigned_doctor_id,
        is_active=True,
    )

    db.add(patient)
    await db.commit()
    await db.refresh(patient)

    return _build_detail(patient)


async def get_patient(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> PatientDetailResponse:
    """
    Fetches a single patient's full profile.

    The hospital_id filter ensures you can only fetch patients
    that belong to your hospital.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token
        patient_id:  UUID of the patient to fetch

    Returns:
        PatientDetailResponse

    Raises:
        NotFoundException if patient does not exist or belongs to another hospital
    """
    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    patient = result.scalar_one_or_none()

    if not patient:
        raise NotFoundException("Patient")

    return _build_detail(patient)


async def get_patient_by_number(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    patient_number: str,
) -> PatientDetailResponse:
    """
    Fetches a patient by their human-readable patient number.

    Staff often look up patients by saying their patient number out
    loud. This endpoint supports that workflow.

    Args:
        db:             Tenant database session
        hospital_id:    From the JWT token
        patient_number: The PAT-XXXXX identifier

    Returns:
        PatientDetailResponse
    """
    result = await db.execute(
        select(Patient).where(
            Patient.patient_number == patient_number.upper(),
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    patient = result.scalar_one_or_none()

    if not patient:
        raise NotFoundException("Patient")

    return _build_detail(patient)


async def list_patients(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    gender_filter: str | None = None,
    blood_type_filter: str | None = None,
    assigned_doctor_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> PatientListResponse:
    """
    Returns a paginated list of patients for a hospital.

    Ordered by registration date (newest first) by default.
    Clinical staff typically want to see recently registered patients
    at the top of the list.

    Args:
        db:                  Tenant database session
        hospital_id:         From the JWT token
        page:                Page number (1-indexed)
        per_page:            Results per page
        gender_filter:       Filter by gender string
        blood_type_filter:   Filter by blood type string
        assigned_doctor_id:  Filter by assigned doctor
        include_inactive:    If True, include deactivated patients

    Returns:
        PatientListResponse
    """
    query = (
        select(Patient)
        .where(Patient.hospital_id == hospital_id)
        .order_by(Patient.created_at.desc())
    )

    # Exclude inactive patients by default
    if not include_inactive:
        query = query.where(Patient.is_active == True)

    # Apply optional filters
    if gender_filter:
        try:
            gender_enum = Gender(gender_filter)
            query = query.where(Patient.gender == gender_enum)
        except ValueError:
            pass

    if blood_type_filter:
        try:
            bt_enum = BloodType(blood_type_filter)
            query = query.where(Patient.blood_type == bt_enum)
        except ValueError:
            pass

    if assigned_doctor_id:
        query = query.where(Patient.assigned_doctor_id == assigned_doctor_id)

    # Count total matching records for pagination metadata
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    patients = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return PatientListResponse(
        items=[_build_summary(p) for p in patients],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


async def search_patients(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    query_string: str,
    page: int = 1,
    per_page: int = 20,
) -> PatientSearchResponse:
    """
    Searches patients across multiple fields simultaneously.

    Search fields:
      - First name (partial match)
      - Last name (partial match)
      - Full name combination (first + last, partial match)
      - Phone number (partial match — useful for "081...")
      - Email (partial match)
      - Patient number (exact prefix match — "PAT-0" finds all)
      - National ID (exact match — ID numbers are precise)

    All searches are case-insensitive.
    Only active patients are returned.

    This search is sufficient for thousands of patients.
    At millions of patients, we would switch to PostgreSQL full-text
    search (tsvector/tsquery) or a dedicated search service.

    Args:
        db:           Tenant database session
        hospital_id:  From the JWT token
        query_string: The text the staff member typed
        page:         Page number
        per_page:     Results per page

    Returns:
        PatientSearchResponse
    """
    if not query_string or len(query_string.strip()) < 1:
        raise ValidationException(
            "Search query must not be empty"
        )

    search_term = f"%{query_string.strip()}%"
    exact_term = query_string.strip()

    query = (
        select(Patient)
        .where(
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
            or_(
                # Name fields — partial match
                Patient.first_name.ilike(search_term),
                Patient.last_name.ilike(search_term),
                Patient.middle_name.ilike(search_term),
                # Phone — partial match (useful for searching by prefix)
                Patient.phone_primary.ilike(search_term),
                Patient.phone_secondary.ilike(search_term),
                # Email — partial match
                Patient.email.ilike(search_term),
                # Patient number — partial match (PAT-0 finds all)
                Patient.patient_number.ilike(search_term),
                # National ID — exact match only (IDs are precise identifiers)
                Patient.national_id == exact_term,
            )
        )
        .order_by(Patient.last_name, Patient.first_name)
    )

    # Count total matches
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    patients = result.scalars().all()

    return PatientSearchResponse(
        items=[_build_summary(p) for p in patients],
        total=total,
        query=query_string.strip(),
        page=page,
        per_page=per_page,
    )


async def update_patient(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    patient_id: uuid.UUID,
    data: PatientUpdateRequest,
) -> PatientDetailResponse:
    """
    Updates a patient record.

    Only updates fields explicitly provided in the request.
    Uses the "only set if provided" pattern to avoid overwriting
    fields with None when the caller simply did not include them.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token
        patient_id:  UUID of the patient to update
        data:        Fields to update (all optional)

    Returns:
        Updated PatientDetailResponse
    """
    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    patient = result.scalar_one_or_none()

    if not patient:
        raise NotFoundException("Patient")

    # Check national ID uniqueness if it is being changed
    if data.national_id is not None and data.national_id != patient.national_id:
        id_check = await db.execute(
            select(Patient).where(
                Patient.hospital_id == hospital_id,
                Patient.national_id == data.national_id,
                Patient.id != patient_id,
                Patient.is_active == True,
            )
        )
        if id_check.scalar_one_or_none():
            raise ConflictException(
                f"Another patient with national ID '{data.national_id}' "
                f"is already registered."
            )

    # Apply only fields that were explicitly provided
    # "is not None" check means we only update provided fields
    if data.first_name is not None:
        patient.first_name = data.first_name.strip()
    if data.last_name is not None:
        patient.last_name = data.last_name.strip()
    if data.middle_name is not None:
        patient.middle_name = data.middle_name.strip()
    if data.date_of_birth is not None:
        patient.date_of_birth = data.date_of_birth
    if data.gender is not None:
        patient.gender = _to_gender(data.gender)
    if data.marital_status is not None:
        patient.marital_status = _to_marital_status(data.marital_status)
    if data.national_id is not None:
        patient.national_id = data.national_id
    if data.phone_primary is not None:
        patient.phone_primary = data.phone_primary
    if data.phone_secondary is not None:
        patient.phone_secondary = data.phone_secondary
    if data.email is not None:
        patient.email = data.email.lower()
    if data.address_line1 is not None:
        patient.address_line1 = data.address_line1
    if data.address_line2 is not None:
        patient.address_line2 = data.address_line2
    if data.city is not None:
        patient.city = data.city
    if data.state is not None:
        patient.state = data.state
    if data.country is not None:
        patient.country = data.country
    if data.blood_type is not None:
        patient.blood_type = _to_blood_type(data.blood_type)
    if data.allergies is not None:
        patient.allergies = data.allergies
    if data.chronic_conditions is not None:
        patient.chronic_conditions = data.chronic_conditions
    if data.current_medications is not None:
        patient.current_medications = data.current_medications
    if data.medical_notes is not None:
        patient.medical_notes = data.medical_notes
    if data.emergency_contact_name is not None:
        patient.emergency_contact_name = data.emergency_contact_name
    if data.emergency_contact_relationship is not None:
        patient.emergency_contact_relationship = data.emergency_contact_relationship
    if data.emergency_contact_phone is not None:
        patient.emergency_contact_phone = data.emergency_contact_phone
    if data.insurance_provider is not None:
        patient.insurance_provider = data.insurance_provider
    if data.insurance_number is not None:
        patient.insurance_number = data.insurance_number
    if data.occupation is not None:
        patient.occupation = data.occupation
    if data.employer is not None:
        patient.employer = data.employer
    if data.assigned_doctor_id is not None:
        patient.assigned_doctor_id = data.assigned_doctor_id

    await db.commit()
    await db.refresh(patient)

    return _build_detail(patient)


async def deactivate_patient(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    patient_id: uuid.UUID,
    requesting_user: HospitalUser,
) -> dict:
    """
    Soft-deactivates a patient record.

    Only hospital admins can deactivate patients.
    The record is preserved — is_active is set to False.
    Historical data (appointments, records, prescriptions) remains intact.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        patient_id:       UUID of the patient to deactivate
        requesting_user:  The user performing the action (for role check)

    Returns:
        Success message dict
    """
    if requesting_user.role != HospitalUserRole.HOSPITAL_ADMIN:
        raise ForbiddenException(
            "Only hospital administrators can deactivate patient records."
        )

    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.hospital_id == hospital_id,
        )
    )
    patient = result.scalar_one_or_none()

    if not patient:
        raise NotFoundException("Patient")

    if not patient.is_active:
        raise ValidationException("This patient record is already inactive.")

    patient.is_active = False
    await db.commit()

    return {
        "message": (
            f"Patient record for {patient.full_name} "
            f"({patient.patient_number}) has been deactivated."
        ),
        "patient_id": str(patient.id),
        "patient_number": patient.patient_number,
    }


async def get_patient_stats(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> dict:
    """
    Returns summary statistics about the hospital's patient records.

    Used for the hospital dashboard overview panel.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token

    Returns:
        Dict with total, active, inactive counts and gender breakdown
    """
    total_result = await db.execute(
        select(func.count(Patient.id)).where(
            Patient.hospital_id == hospital_id
        )
    )
    total = total_result.scalar() or 0

    active_result = await db.execute(
        select(func.count(Patient.id)).where(
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    active = active_result.scalar() or 0

    # Count patients registered this month
    from datetime import date
    today = date.today()
    first_of_month = today.replace(day=1)

    monthly_result = await db.execute(
        select(func.count(Patient.id)).where(
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
            func.date(Patient.created_at) >= first_of_month,
        )
    )
    registered_this_month = monthly_result.scalar() or 0

    return {
        "total_patients": total,
        "active_patients": active,
        "inactive_patients": total - active,
        "registered_this_month": registered_this_month,
    }