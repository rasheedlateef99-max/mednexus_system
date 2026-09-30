"""
Appointment Scheduling Service
---------------------------------
Business logic for appointment management.

The most critical piece of logic in this module is conflict detection.
It runs on every new booking and every reschedule.

Conflict Detection Algorithm:
  Two time intervals [A_start, A_end) and [B_start, B_end) overlap if:
    A_start < B_end AND A_end > B_start

  Applied here:
    new_start < existing_end   AND   new_end > existing_start

  Where:
    existing_end = existing.scheduled_at + timedelta(minutes=existing.duration_minutes)
    new_end      = new_start + timedelta(minutes=duration_minutes)

  We only check appointments that are NOT in a terminal state
  (not cancelled, not completed, not no_show) because a cancelled
  appointment vacates the time slot.

Multi-tenancy:
  Every function receives hospital_id from the JWT token.
  Every database query filters by hospital_id.
  Doctors and patients are validated to belong to the same hospital.

Status Transition Rules (enforced here, not just in schema):
  scheduled   → confirmed, cancelled
  confirmed   → in_progress, cancelled, no_show
  in_progress → completed, cancelled
  completed, cancelled, no_show → terminal (no further changes)
"""

import uuid
from datetime import datetime, timedelta, timezone, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_

from app.models.tenant.appointment import Appointment, AppointmentStatus, AppointmentType
from app.models.tenant.patient import Patient
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.models.tenant.department import Department
from app.schemas.hospital.appointment import (
    AppointmentBookRequest,
    AppointmentUpdateRequest,
    AppointmentStatusUpdateRequest,
    AppointmentSummary,
    AppointmentDetailResponse,
    AppointmentListResponse,
    DoctorScheduleResponse,
    AppointmentStatsResponse,
    STATUS_TRANSITIONS,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    ForbiddenException,
)


# ── Helper: Compute End Time ──────────────────────────────────────────────────

def _end_time(start: datetime, duration_minutes: int) -> datetime:
    """
    Computes the end time of an appointment.

    Args:
        start:            The scheduled start time
        duration_minutes: Duration in minutes

    Returns:
        The computed end datetime
    """
    return start + timedelta(minutes=duration_minutes)


# ── Helper: Enrich Appointment with Names ─────────────────────────────────────

async def _enrich_summary(
    db: AsyncSession,
    appointment: Appointment,
    hospital_id: uuid.UUID,
) -> AppointmentSummary:
    """
    Builds an AppointmentSummary and populates the derived name fields.

    Fetches the patient name, doctor name, and department name from
    related records. These are stored separately and joined here
    rather than using SQLAlchemy relationships — this keeps the
    queries simple and explicit.

    Args:
        db:          Tenant database session
        appointment: The appointment model instance
        hospital_id: For scoping related record lookups

    Returns:
        AppointmentSummary with name fields populated
    """
    summary = AppointmentSummary.model_validate(appointment)

    # Fetch patient name
    patient_result = await db.execute(
        select(Patient.first_name, Patient.last_name).where(
            Patient.id == appointment.patient_id,
            Patient.hospital_id == hospital_id,
        )
    )
    patient_row = patient_result.first()
    if patient_row:
        summary.patient_name = f"{patient_row.first_name} {patient_row.last_name}"

    # Fetch doctor name
    doctor_result = await db.execute(
        select(HospitalUser.full_name).where(
            HospitalUser.id == appointment.doctor_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    doctor_row = doctor_result.first()
    if doctor_row:
        summary.doctor_name = doctor_row.full_name

    # Fetch department name (if applicable)
    if appointment.department_id:
        dept_result = await db.execute(
            select(Department.name).where(
                Department.id == appointment.department_id,
                Department.hospital_id == hospital_id,
            )
        )
        dept_row = dept_result.first()
        if dept_row:
            summary.department_name = dept_row.name

    return summary


async def _enrich_detail(
    db: AsyncSession,
    appointment: Appointment,
    hospital_id: uuid.UUID,
) -> AppointmentDetailResponse:
    """
    Builds an AppointmentDetailResponse with all name fields and end time.
    """
    detail = AppointmentDetailResponse.model_validate(appointment)

    # Compute end time
    detail.end_time = _end_time(
        appointment.scheduled_at,
        appointment.duration_minutes,
    )

    # Fetch patient name
    patient_result = await db.execute(
        select(Patient.first_name, Patient.last_name).where(
            Patient.id == appointment.patient_id,
            Patient.hospital_id == hospital_id,
        )
    )
    patient_row = patient_result.first()
    if patient_row:
        detail.patient_name = f"{patient_row.first_name} {patient_row.last_name}"

    # Fetch doctor name
    doctor_result = await db.execute(
        select(HospitalUser.full_name).where(
            HospitalUser.id == appointment.doctor_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    doctor_row = doctor_result.first()
    if doctor_row:
        detail.doctor_name = doctor_row.full_name

    # Fetch department name
    if appointment.department_id:
        dept_result = await db.execute(
            select(Department.name).where(
                Department.id == appointment.department_id,
                Department.hospital_id == hospital_id,
            )
        )
        dept_row = dept_result.first()
        if dept_row:
            detail.department_name = dept_row.name

    return detail


# ── Conflict Detection ────────────────────────────────────────────────────────

async def _check_doctor_conflict(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    doctor_id: uuid.UUID,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id: uuid.UUID | None = None,
) -> Appointment | None:
    """
    Checks whether a doctor has a conflicting appointment.

    Returns the conflicting appointment if one exists, or None if
    the time slot is available.

    The overlap formula:
      new_start < existing_end AND new_end > existing_start

    We only check active appointments (not cancelled/completed/no_show).

    Args:
        db:                     Tenant database session
        hospital_id:            Tenant scope
        doctor_id:              Doctor being checked
        scheduled_at:           Proposed start time
        duration_minutes:       Proposed duration
        exclude_appointment_id: When rescheduling, exclude the appointment
                                being updated from its own conflict check

    Returns:
        Conflicting Appointment or None
    """
    new_end = _end_time(scheduled_at, duration_minutes)

    # Non-terminal statuses — these occupy the doctor's time
    active_statuses = [
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.IN_PROGRESS,
    ]

    query = select(Appointment).where(
        Appointment.hospital_id == hospital_id,
        Appointment.doctor_id == doctor_id,
        Appointment.status.in_(active_statuses),
        # Overlap condition:
        # new_start < existing_end  →  scheduled_at < existing.scheduled_at + duration
        # new_end > existing_start  →  new_end > existing.scheduled_at
        scheduled_at < func.cast(
            Appointment.scheduled_at,
            type_=Appointment.scheduled_at.type,
        ) + func.cast(
            Appointment.duration_minutes * 60,
            type_=Appointment.duration_minutes.type,
        ),
    )

    if exclude_appointment_id:
        query = query.where(Appointment.id != exclude_appointment_id)

    result = await db.execute(query)
    existing_appointments = result.scalars().all()

    # Check overlap in Python (more reliable than complex SQL interval math)
    for existing in existing_appointments:
        existing_end = _end_time(existing.scheduled_at, existing.duration_minutes)
        # Overlap: new starts before existing ends AND new ends after existing starts
        if scheduled_at < existing_end and new_end > existing.scheduled_at:
            return existing

    return None


# ── Core Appointment Operations ───────────────────────────────────────────────

async def book_appointment(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    booking_user: HospitalUser,
    data: AppointmentBookRequest,
) -> AppointmentDetailResponse:
    """
    Books a new appointment.

    Validation sequence:
      1. Patient exists and belongs to this hospital
      2. Doctor exists, belongs to this hospital, and has role=doctor
      3. Department (if provided) exists and belongs to this hospital
      4. No conflicting appointment exists for the doctor at this time

    Args:
        db:           Tenant database session
        hospital_id:  From the JWT token
        booking_user: The staff member making the booking
        data:         Appointment details from the request body

    Returns:
        AppointmentDetailResponse
    """

    # ── Step 1: Validate patient ──────────────────────────────────────────────
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == data.patient_id,
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    if not patient_result.scalar_one_or_none():
        raise NotFoundException("Patient")

    # ── Step 2: Validate doctor ───────────────────────────────────────────────
    doctor_result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == data.doctor_id,
            HospitalUser.hospital_id == hospital_id,
            HospitalUser.role == HospitalUserRole.DOCTOR,
        )
    )
    doctor = doctor_result.scalar_one_or_none()
    if not doctor:
        raise NotFoundException(
            "Doctor — the specified user does not exist or is not a doctor "
            "in this hospital"
        )

    # ── Step 3: Validate department ───────────────────────────────────────────
    if data.department_id:
        dept_result = await db.execute(
            select(Department).where(
                Department.id == data.department_id,
                Department.hospital_id == hospital_id,
                Department.is_active == True,
            )
        )
        if not dept_result.scalar_one_or_none():
            raise NotFoundException("Department")

    # ── Step 4: Conflict detection ────────────────────────────────────────────
    conflict = await _check_doctor_conflict(
        db=db,
        hospital_id=hospital_id,
        doctor_id=data.doctor_id,
        scheduled_at=data.scheduled_at,
        duration_minutes=data.duration_minutes,
    )

    if conflict:
        conflict_end = _end_time(conflict.scheduled_at, conflict.duration_minutes)
        raise ConflictException(
            f"Dr. {doctor.full_name} already has an appointment from "
            f"{conflict.scheduled_at.strftime('%H:%M')} to "
            f"{conflict_end.strftime('%H:%M')} on "
            f"{conflict.scheduled_at.strftime('%Y-%m-%d')}. "
            f"Please choose a different time slot."
        )

    # ── Create the appointment ────────────────────────────────────────────────
    appointment = Appointment(
        hospital_id=hospital_id,
        patient_id=data.patient_id,
        doctor_id=data.doctor_id,
        department_id=data.department_id,
        scheduled_at=data.scheduled_at,
        duration_minutes=data.duration_minutes,
        appointment_type=AppointmentType(data.appointment_type),
        status=AppointmentStatus.SCHEDULED,
        chief_complaint=data.chief_complaint,
        notes=data.notes,
        booked_by=booking_user.id,
    )

    db.add(appointment)
    await db.commit()
    await db.refresh(appointment)

    return await _enrich_detail(db, appointment, hospital_id)


async def get_appointment(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    appointment_id: uuid.UUID,
) -> AppointmentDetailResponse:
    """
    Fetches a single appointment by ID.

    Args:
        db:             Tenant database session
        hospital_id:    From the JWT token
        appointment_id: UUID of the appointment

    Returns:
        AppointmentDetailResponse

    Raises:
        NotFoundException if not found or belongs to another hospital
    """
    result = await db.execute(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.hospital_id == hospital_id,
        )
    )
    appointment = result.scalar_one_or_none()

    if not appointment:
        raise NotFoundException("Appointment")

    return await _enrich_detail(db, appointment, hospital_id)


async def list_appointments(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    doctor_id: uuid.UUID | None = None,
    patient_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    appointment_type_filter: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> AppointmentListResponse:
    """
    Returns a paginated list of appointments with optional filters.

    Ordered by scheduled_at ascending (upcoming first) by default.

    Args:
        db:                      Tenant database session
        hospital_id:             From the JWT token
        page:                    Page number
        per_page:                Results per page
        doctor_id:               Filter by doctor
        patient_id:              Filter by patient
        status_filter:           Filter by status string
        appointment_type_filter: Filter by appointment type
        date_from:               Filter appointments from this datetime
        date_to:                 Filter appointments up to this datetime

    Returns:
        AppointmentListResponse
    """
    query = (
        select(Appointment)
        .where(Appointment.hospital_id == hospital_id)
        .order_by(Appointment.scheduled_at.asc())
    )

    # Apply filters
    if doctor_id:
        query = query.where(Appointment.doctor_id == doctor_id)

    if patient_id:
        query = query.where(Appointment.patient_id == patient_id)

    if status_filter:
        try:
            status_enum = AppointmentStatus(status_filter)
            query = query.where(Appointment.status == status_enum)
        except ValueError:
            pass

    if appointment_type_filter:
        try:
            type_enum = AppointmentType(appointment_type_filter)
            query = query.where(Appointment.appointment_type == type_enum)
        except ValueError:
            pass

    if date_from:
        query = query.where(Appointment.scheduled_at >= date_from)

    if date_to:
        query = query.where(Appointment.scheduled_at <= date_to)

    # Count total
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    appointments = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    items = []
    for appt in appointments:
        items.append(await _enrich_summary(db, appt, hospital_id))

    return AppointmentListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


async def update_appointment(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    appointment_id: uuid.UUID,
    data: AppointmentUpdateRequest,
    requesting_user: HospitalUser,
) -> AppointmentDetailResponse:
    """
    Updates the scheduling details of an appointment.

    Cannot update terminal appointments (completed, cancelled, no_show).
    If rescheduling (changing scheduled_at or duration), conflict detection
    runs again excluding the current appointment.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        appointment_id:   UUID of the appointment to update
        data:             Fields to update
        requesting_user:  For permission checks (future use)

    Returns:
        Updated AppointmentDetailResponse
    """
    result = await db.execute(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.hospital_id == hospital_id,
        )
    )
    appointment = result.scalar_one_or_none()

    if not appointment:
        raise NotFoundException("Appointment")

    # Cannot modify terminal appointments
    terminal_statuses = {
        AppointmentStatus.COMPLETED,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    }
    if appointment.status in terminal_statuses:
        raise ValidationException(
            f"Cannot modify a {appointment.status.value} appointment."
        )

    # If rescheduling, check for conflicts
    new_scheduled_at = data.scheduled_at or appointment.scheduled_at
    new_duration = data.duration_minutes or appointment.duration_minutes
    new_doctor_id = data.doctor_id or appointment.doctor_id

    rescheduling = (
        data.scheduled_at is not None
        or data.duration_minutes is not None
        or data.doctor_id is not None
    )

    if rescheduling:
        conflict = await _check_doctor_conflict(
            db=db,
            hospital_id=hospital_id,
            doctor_id=new_doctor_id,
            scheduled_at=new_scheduled_at,
            duration_minutes=new_duration,
            exclude_appointment_id=appointment_id,
        )
        if conflict:
            conflict_end = _end_time(conflict.scheduled_at, conflict.duration_minutes)
            raise ConflictException(
                f"The doctor already has an appointment from "
                f"{conflict.scheduled_at.strftime('%H:%M')} to "
                f"{conflict_end.strftime('%H:%M')}. "
                f"Please choose a different time."
            )

    # Apply updates
    if data.scheduled_at is not None:
        appointment.scheduled_at = data.scheduled_at
    if data.duration_minutes is not None:
        appointment.duration_minutes = data.duration_minutes
    if data.department_id is not None:
        appointment.department_id = data.department_id
    if data.appointment_type is not None:
        appointment.appointment_type = AppointmentType(data.appointment_type)
    if data.chief_complaint is not None:
        appointment.chief_complaint = data.chief_complaint
    if data.notes is not None:
        appointment.notes = data.notes
    if data.doctor_id is not None:
        appointment.doctor_id = data.doctor_id

    await db.commit()
    await db.refresh(appointment)

    return await _enrich_detail(db, appointment, hospital_id)


async def update_appointment_status(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    appointment_id: uuid.UUID,
    data: AppointmentStatusUpdateRequest,
    requesting_user: HospitalUser,
) -> AppointmentDetailResponse:
    """
    Transitions an appointment to a new status.

    Enforces the status transition rules defined in STATUS_TRANSITIONS.
    Requires a cancellation reason when cancelling.
    Records timestamps for started_at and completed_at.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        appointment_id:   UUID of the appointment
        data:             New status and optional notes/reason
        requesting_user:  The user performing the action

    Returns:
        Updated AppointmentDetailResponse
    """
    result = await db.execute(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.hospital_id == hospital_id,
        )
    )
    appointment = result.scalar_one_or_none()

    if not appointment:
        raise NotFoundException("Appointment")

    current_status = appointment.status.value
    new_status = data.status

    # Validate transition is allowed
    allowed_transitions = STATUS_TRANSITIONS.get(current_status, set())
    if new_status not in allowed_transitions:
        if not allowed_transitions:
            raise ValidationException(
                f"This appointment is {current_status} and cannot be changed."
            )
        raise ValidationException(
            f"Cannot change status from '{current_status}' to '{new_status}'. "
            f"Allowed transitions: {', '.join(sorted(allowed_transitions))}"
        )

    # Require cancellation reason
    if new_status == "cancelled" and not data.cancellation_reason:
        raise ValidationException(
            "A cancellation reason is required when cancelling an appointment."
        )

    # Apply status-specific fields
    now = datetime.now(timezone.utc)

    appointment.status = AppointmentStatus(new_status)

    if new_status == "cancelled":
        appointment.cancelled_at = now
        appointment.cancellation_reason = data.cancellation_reason
        appointment.cancelled_by = requesting_user.id

    elif new_status == "in_progress":
        appointment.started_at = now

    elif new_status == "completed":
        appointment.completed_at = now

    if data.notes:
        appointment.notes = (appointment.notes or "") + f"\n[{now.strftime('%Y-%m-%d %H:%M')}] {data.notes}"

    await db.commit()
    await db.refresh(appointment)

    return await _enrich_detail(db, appointment, hospital_id)


async def get_doctor_schedule(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    doctor_id: uuid.UUID,
    schedule_date: date,
) -> DoctorScheduleResponse:
    """
    Returns a doctor's complete schedule for a specific day.

    This is the "today's schedule" view that a doctor sees when
    they log in. Ordered by scheduled_at ascending.

    Args:
        db:            Tenant database session
        hospital_id:   From the JWT token
        doctor_id:     UUID of the doctor
        schedule_date: The date to fetch the schedule for

    Returns:
        DoctorScheduleResponse
    """
    from datetime import datetime, time

    # Build day boundaries in UTC
    day_start = datetime.combine(schedule_date, time.min).replace(
        tzinfo=timezone.utc
    )
    day_end = datetime.combine(schedule_date, time.max).replace(
        tzinfo=timezone.utc
    )

    # Validate doctor exists in this hospital
    doctor_result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == doctor_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    doctor = doctor_result.scalar_one_or_none()
    if not doctor:
        raise NotFoundException("Doctor")

    result = await db.execute(
        select(Appointment)
        .where(
            Appointment.hospital_id == hospital_id,
            Appointment.doctor_id == doctor_id,
            Appointment.scheduled_at >= day_start,
            Appointment.scheduled_at <= day_end,
        )
        .order_by(Appointment.scheduled_at.asc())
    )
    appointments = result.scalars().all()

    items = []
    for appt in appointments:
        items.append(await _enrich_summary(db, appt, hospital_id))

    return DoctorScheduleResponse(
        doctor_id=doctor.id,
        doctor_name=doctor.full_name,
        date=schedule_date.isoformat(),
        appointments=items,
        total_appointments=len(items),
    )


async def get_appointment_stats(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> AppointmentStatsResponse:
    """
    Returns appointment statistics for the hospital dashboard.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token

    Returns:
        AppointmentStatsResponse
    """
    from datetime import datetime, time, timedelta

    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min).replace(tzinfo=timezone.utc)
    today_end = datetime.combine(now.date(), time.max).replace(tzinfo=timezone.utc)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    async def count_by_status(status: AppointmentStatus, start: datetime, end: datetime) -> int:
        result = await db.execute(
            select(func.count(Appointment.id)).where(
                Appointment.hospital_id == hospital_id,
                Appointment.status == status,
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at <= end,
            )
        )
        return result.scalar() or 0

    async def count_total(start: datetime, end: datetime) -> int:
        result = await db.execute(
            select(func.count(Appointment.id)).where(
                Appointment.hospital_id == hospital_id,
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at <= end,
            )
        )
        return result.scalar() or 0

    return AppointmentStatsResponse(
        total_today=await count_total(today_start, today_end),
        scheduled_today=await count_by_status(AppointmentStatus.SCHEDULED, today_start, today_end),
        confirmed_today=await count_by_status(AppointmentStatus.CONFIRMED, today_start, today_end),
        in_progress_today=await count_by_status(AppointmentStatus.IN_PROGRESS, today_start, today_end),
        completed_today=await count_by_status(AppointmentStatus.COMPLETED, today_start, today_end),
        cancelled_today=await count_by_status(AppointmentStatus.CANCELLED, today_start, today_end),
        no_show_today=await count_by_status(AppointmentStatus.NO_SHOW, today_start, today_end),
        total_this_week=await count_total(week_start, today_end),
        total_this_month=await count_total(month_start, today_end),
    )