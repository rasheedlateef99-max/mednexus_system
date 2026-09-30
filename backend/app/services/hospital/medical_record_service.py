"""
Medical Record Service
------------------------
Business logic for clinical documentation.

Core responsibilities:
  1. Record creation       — link to patient, doctor, appointment
  2. Draft management      — progressive saving during encounter
  3. Finalisation          — doctor signs the record
  4. Locking               — automatic read-only after 24h
  5. Amendment tracking    — post-finalisation edits are logged
  6. Patient history       — chronological view of all records
  7. Access control        — only clinical staff can write records

Record Locking Logic:
  When a record is finalised, lock_after is set to finalised_at + 24h.
  On every read/write request, we check:
    if now > lock_after → set status to 'locked', return locked record
  This check happens in _check_and_apply_lock() which is called at
  the start of every service function that touches a record.

Amendment Audit Trail:
  When an amendment is made, the following is logged in amendment_history:
    - Timestamp of the amendment
    - UUID of the doctor who amended it
    - Name of the doctor
    - Reason given for the amendment
    - List of field names that were changed
  This log is append-only. Entries are never deleted.

One-Appointment-One-Record Rule:
  If an appointment_id is provided, we check whether a record
  already exists for that appointment. If one exists, we return
  a ConflictException with the existing record's ID.
  The frontend should redirect to the existing record rather than
  creating a duplicate.
"""

import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.tenant.medical_record import MedicalRecord, RecordStatus
from app.models.tenant.patient import Patient
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.models.tenant.appointment import Appointment, AppointmentStatus
from app.schemas.hospital.medical_record import (
    MedicalRecordCreateRequest,
    MedicalRecordUpdateRequest,
    MedicalRecordAmendRequest,
    MedicalRecordSummary,
    MedicalRecordDetailResponse,
    MedicalRecordListResponse,
    PatientMedicalHistoryResponse,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    ForbiddenException,
)

# How long after finalisation a record can be amended
AMENDMENT_WINDOW_HOURS = 24


# ── Lock Enforcement ──────────────────────────────────────────────────────────

async def _check_and_apply_lock(
    db: AsyncSession,
    record: MedicalRecord,
) -> MedicalRecord:
    """
    Checks whether a finalised record has passed its amendment window.

    If the current time is past lock_after, the record status is
    set to 'locked' and saved. This is called at the start of every
    function that reads or writes a record.

    Why we do this lazily (on access) rather than with a scheduled job:
      - No background worker infrastructure needed
      - Simpler to reason about
      - The lock happens the first time the record is accessed after expiry
      - Suitable for current scale; can switch to a scheduled task later

    Args:
        db:     Tenant database session
        record: The medical record to check

    Returns:
        The record (status updated to 'locked' if applicable)
    """
    if (
        record.status in (RecordStatus.FINALISED, RecordStatus.AMENDED)
        and record.lock_after is not None
    ):
        now = datetime.now(timezone.utc)
        # Ensure lock_after is timezone-aware for comparison
        lock_after = record.lock_after
        if lock_after.tzinfo is None:
            lock_after = lock_after.replace(tzinfo=timezone.utc)

        if now > lock_after:
            record.status = RecordStatus.LOCKED
            await db.commit()
            await db.refresh(record)

    return record


# ── Computed Fields ───────────────────────────────────────────────────────────

def _compute_lock_flags(record: MedicalRecord) -> tuple[bool, bool]:
    """
    Computes the is_locked and can_amend flags for a record.

    Returns:
        (is_locked, can_amend)
        is_locked:  True if record status is 'locked'
        can_amend:  True if record is finalised/amended and within 24h window
    """
    is_locked = record.status == RecordStatus.LOCKED

    can_amend = False
    if record.status in (RecordStatus.FINALISED, RecordStatus.AMENDED):
        if record.lock_after is not None:
            now = datetime.now(timezone.utc)
            lock_after = record.lock_after
            if lock_after.tzinfo is None:
                lock_after = lock_after.replace(tzinfo=timezone.utc)
            can_amend = now <= lock_after

    return is_locked, can_amend


# ── Name Enrichment ───────────────────────────────────────────────────────────

async def _get_patient_name(
    db: AsyncSession,
    patient_id: uuid.UUID,
    hospital_id: uuid.UUID,
) -> str:
    result = await db.execute(
        select(Patient.first_name, Patient.last_name).where(
            Patient.id == patient_id,
            Patient.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return f"{row.first_name} {row.last_name}" if row else ""


async def _get_doctor_name(
    db: AsyncSession,
    doctor_id: uuid.UUID,
    hospital_id: uuid.UUID,
) -> str:
    result = await db.execute(
        select(HospitalUser.full_name).where(
            HospitalUser.id == doctor_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return row.full_name if row else ""


async def _build_summary(
    db: AsyncSession,
    record: MedicalRecord,
    hospital_id: uuid.UUID,
) -> MedicalRecordSummary:
    """Builds a MedicalRecordSummary with computed and enriched fields."""
    summary = MedicalRecordSummary.model_validate(record)
    summary.patient_name = await _get_patient_name(db, record.patient_id, hospital_id)
    summary.doctor_name = await _get_doctor_name(db, record.doctor_id, hospital_id)
    is_locked, can_amend = _compute_lock_flags(record)
    summary.is_locked = is_locked
    summary.can_amend = can_amend
    return summary


async def _build_detail(
    db: AsyncSession,
    record: MedicalRecord,
    hospital_id: uuid.UUID,
) -> MedicalRecordDetailResponse:
    """Builds a MedicalRecordDetailResponse with all fields."""
    detail = MedicalRecordDetailResponse.model_validate(record)
    detail.patient_name = await _get_patient_name(db, record.patient_id, hospital_id)
    detail.doctor_name = await _get_doctor_name(db, record.doctor_id, hospital_id)
    is_locked, can_amend = _compute_lock_flags(record)
    detail.is_locked = is_locked
    detail.can_amend = can_amend
    return detail


# ── Core Service Functions ────────────────────────────────────────────────────

async def create_medical_record(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    creating_doctor: HospitalUser,
    data: MedicalRecordCreateRequest,
) -> MedicalRecordDetailResponse:
    """
    Creates a new medical record in draft status.

    Validation sequence:
      1. Creating user must be clinical staff
      2. Patient exists and belongs to this hospital
      3. Doctor exists and belongs to this hospital
      4. If appointment_id provided: appointment exists, belongs to hospital,
         and does not already have a medical record

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        creating_doctor:  Must be clinical staff
        data:             Record content from the request body

    Returns:
        MedicalRecordDetailResponse (status: draft)
    """

    # Step 1: Validate creating user is clinical staff
    clinical_roles = {
        HospitalUserRole.HOSPITAL_ADMIN,
        HospitalUserRole.DOCTOR,
        HospitalUserRole.NURSE,
    }
    if creating_doctor.role not in clinical_roles:
        raise ForbiddenException(
            "Only clinical staff (doctors, nurses, administrators) "
            "can create medical records."
        )

    # Step 2: Validate patient
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == data.patient_id,
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    if not patient_result.scalar_one_or_none():
        raise NotFoundException("Patient")

    # Step 3: Validate doctor (the assigned doctor, not necessarily the creator)
    doctor_result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == data.doctor_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    if not doctor_result.scalar_one_or_none():
        raise NotFoundException("Doctor")

    # Step 4: Validate appointment and one-record rule
    if data.appointment_id:
        appt_result = await db.execute(
            select(Appointment).where(
                Appointment.id == data.appointment_id,
                Appointment.hospital_id == hospital_id,
            )
        )
        appointment = appt_result.scalar_one_or_none()
        if not appointment:
            raise NotFoundException("Appointment")

        # Check for existing record for this appointment
        existing_result = await db.execute(
            select(MedicalRecord).where(
                MedicalRecord.appointment_id == data.appointment_id,
                MedicalRecord.hospital_id == hospital_id,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            raise ConflictException(
                f"A medical record already exists for this appointment. "
                f"Existing record ID: {existing.id}. "
                f"Please open the existing record instead of creating a new one."
            )

    # Create the record
    vital_signs_dict = {}
    if data.vital_signs:
        vital_signs_dict = data.vital_signs.model_dump(exclude_none=True)

    record = MedicalRecord(
        hospital_id=hospital_id,
        patient_id=data.patient_id,
        doctor_id=data.doctor_id,
        appointment_id=data.appointment_id,
        chief_complaint=data.chief_complaint,
        history_of_presenting_illness=data.history_of_presenting_illness,
        past_medical_history=data.past_medical_history,
        family_history=data.family_history,
        social_history=data.social_history,
        current_medications_note=data.current_medications_note,
        allergies_noted=data.allergies_noted,
        examination_findings=data.examination_findings,
        vital_signs=vital_signs_dict,
        investigation_results=data.investigation_results,
        diagnosis=data.diagnosis,
        icd10_code=data.icd10_code,
        icd10_description=data.icd10_description,
        secondary_diagnoses=[
            sd.model_dump() for sd in data.secondary_diagnoses
        ],
        clinical_notes=data.clinical_notes,
        treatment_plan=data.treatment_plan,
        follow_up_instructions=data.follow_up_instructions,
        referral_details=data.referral_details,
        sick_leave_days=data.sick_leave_days,
        status=RecordStatus.DRAFT,
        amendment_history=[],
    )

    db.add(record)
    await db.commit()
    await db.refresh(record)

    return await _build_detail(db, record, hospital_id)


async def get_medical_record(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    record_id: uuid.UUID,
) -> MedicalRecordDetailResponse:
    """
    Fetches a single medical record by ID.

    Applies lock check on every read — a finalised record that has
    passed its window becomes locked on first access after expiry.
    """
    result = await db.execute(
        select(MedicalRecord).where(
            MedicalRecord.id == record_id,
            MedicalRecord.hospital_id == hospital_id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise NotFoundException("Medical record")

    record = await _check_and_apply_lock(db, record)
    return await _build_detail(db, record, hospital_id)


async def update_medical_record(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    record_id: uuid.UUID,
    data: MedicalRecordUpdateRequest,
    requesting_user: HospitalUser,
) -> MedicalRecordDetailResponse:
    """
    Updates a draft medical record.

    Only permitted on records with status 'draft'.
    For post-finalisation edits, use amend_medical_record instead.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        record_id:        UUID of the record to update
        data:             Fields to update
        requesting_user:  Must be clinical staff

    Returns:
        Updated MedicalRecordDetailResponse
    """
    result = await db.execute(
        select(MedicalRecord).where(
            MedicalRecord.id == record_id,
            MedicalRecord.hospital_id == hospital_id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise NotFoundException("Medical record")

    record = await _check_and_apply_lock(db, record)

    if record.status != RecordStatus.DRAFT:
        if record.status == RecordStatus.LOCKED:
            raise ForbiddenException(
                "This record is locked and cannot be edited. "
                "Medical records are locked 24 hours after finalisation."
            )
        raise ValidationException(
            f"This record has status '{record.status.value}' and cannot be updated. "
            f"Use the amend endpoint for finalised records."
        )

    # Apply only provided fields
    if data.chief_complaint is not None:
        record.chief_complaint = data.chief_complaint
    if data.history_of_presenting_illness is not None:
        record.history_of_presenting_illness = data.history_of_presenting_illness
    if data.past_medical_history is not None:
        record.past_medical_history = data.past_medical_history
    if data.family_history is not None:
        record.family_history = data.family_history
    if data.social_history is not None:
        record.social_history = data.social_history
    if data.current_medications_note is not None:
        record.current_medications_note = data.current_medications_note
    if data.allergies_noted is not None:
        record.allergies_noted = data.allergies_noted
    if data.examination_findings is not None:
        record.examination_findings = data.examination_findings
    if data.vital_signs is not None:
        record.vital_signs = data.vital_signs.model_dump(exclude_none=True)
    if data.investigation_results is not None:
        record.investigation_results = data.investigation_results
    if data.diagnosis is not None:
        record.diagnosis = data.diagnosis
    if data.icd10_code is not None:
        record.icd10_code = data.icd10_code
    if data.icd10_description is not None:
        record.icd10_description = data.icd10_description
    if data.secondary_diagnoses is not None:
        record.secondary_diagnoses = [
            sd.model_dump() for sd in data.secondary_diagnoses
        ]
    if data.clinical_notes is not None:
        record.clinical_notes = data.clinical_notes
    if data.treatment_plan is not None:
        record.treatment_plan = data.treatment_plan
    if data.follow_up_instructions is not None:
        record.follow_up_instructions = data.follow_up_instructions
    if data.referral_details is not None:
        record.referral_details = data.referral_details
    if data.sick_leave_days is not None:
        record.sick_leave_days = data.sick_leave_days

    await db.commit()
    await db.refresh(record)

    return await _build_detail(db, record, hospital_id)


async def finalise_medical_record(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    record_id: uuid.UUID,
    requesting_user: HospitalUser,
) -> MedicalRecordDetailResponse:
    """
    Finalises a medical record (doctor's signature equivalent).

    Sets status to 'finalised', records the finalisation timestamp,
    and sets the lock_after time to finalised_at + 24 hours.

    If the record is linked to an appointment, the appointment status
    is automatically moved to 'completed'.

    Only the authoring doctor or a hospital admin can finalise a record.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        record_id:        UUID of the record to finalise
        requesting_user:  Must be the authoring doctor or admin

    Returns:
        Updated MedicalRecordDetailResponse
    """
    result = await db.execute(
        select(MedicalRecord).where(
            MedicalRecord.id == record_id,
            MedicalRecord.hospital_id == hospital_id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise NotFoundException("Medical record")

    if record.status != RecordStatus.DRAFT:
        raise ValidationException(
            f"Only draft records can be finalised. "
            f"Current status: {record.status.value}"
        )

    # Only the authoring doctor or an admin can finalise
    is_author = requesting_user.id == record.doctor_id
    is_admin = requesting_user.role == HospitalUserRole.HOSPITAL_ADMIN
    if not is_author and not is_admin:
        raise ForbiddenException(
            "Only the authoring doctor or a hospital administrator "
            "can finalise a medical record."
        )

    now = datetime.now(timezone.utc)
    record.status = RecordStatus.FINALISED
    record.finalised_at = now
    record.lock_after = now + timedelta(hours=AMENDMENT_WINDOW_HOURS)

    # Auto-complete linked appointment
    if record.appointment_id:
        appt_result = await db.execute(
            select(Appointment).where(
                Appointment.id == record.appointment_id,
                Appointment.hospital_id == hospital_id,
            )
        )
        appointment = appt_result.scalar_one_or_none()
        if appointment and appointment.status == AppointmentStatus.IN_PROGRESS:
            appointment.status = AppointmentStatus.COMPLETED
            appointment.completed_at = now

    await db.commit()
    await db.refresh(record)

    return await _build_detail(db, record, hospital_id)


async def amend_medical_record(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    record_id: uuid.UUID,
    data: MedicalRecordAmendRequest,
    requesting_user: HospitalUser,
) -> MedicalRecordDetailResponse:
    """
    Amends a finalised medical record within the 24-hour window.

    Logs the amendment in amendment_history before applying changes.
    Sets status to 'amended' to mark that post-finalisation edits occurred.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        record_id:        UUID of the record to amend
        data:             Amendment reason and fields to change
        requesting_user:  Must be clinical staff

    Returns:
        Updated MedicalRecordDetailResponse
    """
    result = await db.execute(
        select(MedicalRecord).where(
            MedicalRecord.id == record_id,
            MedicalRecord.hospital_id == hospital_id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise NotFoundException("Medical record")

    record = await _check_and_apply_lock(db, record)

    if record.status == RecordStatus.LOCKED:
        raise ForbiddenException(
            "This record is locked. "
            "Medical records cannot be amended more than 24 hours "
            "after finalisation."
        )

    if record.status == RecordStatus.DRAFT:
        raise ValidationException(
            "Draft records should be updated using the update endpoint, "
            "not the amend endpoint. Finalise the record first."
        )

    if record.status not in (RecordStatus.FINALISED, RecordStatus.AMENDED):
        raise ValidationException(
            f"Cannot amend a record with status '{record.status.value}'."
        )

    # Track which fields are being changed for the audit log
    fields_changed = []

    def _apply_and_track(field_name: str, new_value, current_value):
        """Applies a field change and records it in fields_changed."""
        if new_value is not None and new_value != current_value:
            fields_changed.append(field_name)
            return new_value
        return current_value

    record.chief_complaint = _apply_and_track(
        "chief_complaint", data.chief_complaint, record.chief_complaint
    )
    record.history_of_presenting_illness = _apply_and_track(
        "history_of_presenting_illness",
        data.history_of_presenting_illness,
        record.history_of_presenting_illness,
    )
    record.past_medical_history = _apply_and_track(
        "past_medical_history", data.past_medical_history, record.past_medical_history
    )
    record.family_history = _apply_and_track(
        "family_history", data.family_history, record.family_history
    )
    record.social_history = _apply_and_track(
        "social_history", data.social_history, record.social_history
    )
    record.current_medications_note = _apply_and_track(
        "current_medications_note",
        data.current_medications_note,
        record.current_medications_note,
    )
    record.allergies_noted = _apply_and_track(
        "allergies_noted", data.allergies_noted, record.allergies_noted
    )
    record.examination_findings = _apply_and_track(
        "examination_findings", data.examination_findings, record.examination_findings
    )
    if data.vital_signs is not None:
        new_vitals = data.vital_signs.model_dump(exclude_none=True)
        if new_vitals != record.vital_signs:
            fields_changed.append("vital_signs")
            record.vital_signs = new_vitals
    record.investigation_results = _apply_and_track(
        "investigation_results",
        data.investigation_results,
        record.investigation_results,
    )
    record.diagnosis = _apply_and_track(
        "diagnosis", data.diagnosis, record.diagnosis
    )
    record.icd10_code = _apply_and_track(
        "icd10_code", data.icd10_code, record.icd10_code
    )
    record.icd10_description = _apply_and_track(
        "icd10_description", data.icd10_description, record.icd10_description
    )
    if data.secondary_diagnoses is not None:
        new_secondary = [sd.model_dump() for sd in data.secondary_diagnoses]
        if new_secondary != record.secondary_diagnoses:
            fields_changed.append("secondary_diagnoses")
            record.secondary_diagnoses = new_secondary
    record.clinical_notes = _apply_and_track(
        "clinical_notes", data.clinical_notes, record.clinical_notes
    )
    record.treatment_plan = _apply_and_track(
        "treatment_plan", data.treatment_plan, record.treatment_plan
    )
    record.follow_up_instructions = _apply_and_track(
        "follow_up_instructions",
        data.follow_up_instructions,
        record.follow_up_instructions,
    )
    record.referral_details = _apply_and_track(
        "referral_details", data.referral_details, record.referral_details
    )
    if data.sick_leave_days is not None:
        if data.sick_leave_days != record.sick_leave_days:
            fields_changed.append("sick_leave_days")
            record.sick_leave_days = data.sick_leave_days

    # Log the amendment
    now = datetime.now(timezone.utc)
    amendment_entry = {
        "amended_at": now.isoformat(),
        "amended_by": str(requesting_user.id),
        "amended_by_name": requesting_user.full_name,
        "reason": data.amendment_reason,
        "fields_changed": fields_changed,
    }
    record.amendment_history = record.amendment_history + [amendment_entry]
    record.status = RecordStatus.AMENDED

    await db.commit()
    await db.refresh(record)

    return await _build_detail(db, record, hospital_id)


async def list_medical_records(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    diagnosis_search: str | None = None,
    icd10_filter: str | None = None,
) -> MedicalRecordListResponse:
    """
    Returns a paginated list of medical records.

    Ordered by created_at descending (most recent first).

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        page:             Page number
        per_page:         Results per page
        patient_id:       Filter by patient
        doctor_id:        Filter by doctor
        status_filter:    Filter by record status
        diagnosis_search: Search by diagnosis text
        icd10_filter:     Filter by exact ICD-10 code

    Returns:
        MedicalRecordListResponse
    """
    query = (
        select(MedicalRecord)
        .where(MedicalRecord.hospital_id == hospital_id)
        .order_by(MedicalRecord.created_at.desc())
    )

    if patient_id:
        query = query.where(MedicalRecord.patient_id == patient_id)

    if doctor_id:
        query = query.where(MedicalRecord.doctor_id == doctor_id)

    if status_filter:
        try:
            status_enum = RecordStatus(status_filter)
            query = query.where(MedicalRecord.status == status_enum)
        except ValueError:
            pass

    if diagnosis_search:
        query = query.where(
            MedicalRecord.diagnosis.ilike(f"%{diagnosis_search}%")
        )

    if icd10_filter:
        query = query.where(MedicalRecord.icd10_code == icd10_filter.upper())

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    records = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    items = []
    for rec in records:
        rec = await _check_and_apply_lock(db, rec)
        items.append(await _build_summary(db, rec, hospital_id))

    return MedicalRecordListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


async def get_patient_medical_history(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> PatientMedicalHistoryResponse:
    """
    Returns all medical records for one patient, most recent first.

    This is the complete longitudinal clinical history view.
    Used by doctors before a consultation to review previous visits.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token
        patient_id:  UUID of the patient

    Returns:
        PatientMedicalHistoryResponse
    """
    # Verify patient exists
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.hospital_id == hospital_id,
        )
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise NotFoundException("Patient")

    result = await db.execute(
        select(MedicalRecord)
        .where(
            MedicalRecord.patient_id == patient_id,
            MedicalRecord.hospital_id == hospital_id,
        )
        .order_by(MedicalRecord.created_at.desc())
    )
    records = result.scalars().all()

    items = []
    for rec in records:
        rec = await _check_and_apply_lock(db, rec)
        items.append(await _build_summary(db, rec, hospital_id))

    return PatientMedicalHistoryResponse(
        patient_id=patient.id,
        patient_name=patient.full_name,
        total_records=len(items),
        records=items,
    )