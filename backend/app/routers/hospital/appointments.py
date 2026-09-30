"""
Hospital Appointment Router
-----------------------------
HTTP endpoints for appointment scheduling and management.

Route ordering note:
  /stats, /doctor-schedule, and other specific paths are registered
  BEFORE /{appointment_id} to prevent FastAPI treating "stats" as
  a UUID parameter. This is the same pattern used in the patients router.

Endpoints:
  POST   /hospital/appointments/                      → Book appointment
  GET    /hospital/appointments/                      → List appointments
  GET    /hospital/appointments/stats                 → Dashboard statistics
  GET    /hospital/appointments/doctor-schedule       → Doctor's daily schedule
  GET    /hospital/appointments/{id}                  → Get single appointment
  PUT    /hospital/appointments/{id}                  → Update scheduling details
  PATCH  /hospital/appointments/{id}/status           → Update status
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from datetime import datetime, date

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user, require_hospital_admin
from app.schemas.hospital.appointment import (
    AppointmentBookRequest,
    AppointmentUpdateRequest,
    AppointmentStatusUpdateRequest,
    AppointmentDetailResponse,
    AppointmentListResponse,
    DoctorScheduleResponse,
    AppointmentStatsResponse,
)
from app.services.hospital.appointment_service import (
    book_appointment,
    get_appointment,
    list_appointments,
    update_appointment,
    update_appointment_status,
    get_doctor_schedule,
    get_appointment_stats,
)

router = APIRouter()


@router.post(
    "/",
    response_model=AppointmentDetailResponse,
    status_code=201,
    summary="Book Appointment",
    description=(
        "Books a new appointment. "
        "Validates patient, doctor, and department existence. "
        "Runs conflict detection — rejects if the doctor is already booked. "
        "Available to all hospital staff."
    ),
)
async def book(
    data: AppointmentBookRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await book_appointment(
        db=db,
        hospital_id=ctx["hospital_id"],
        booking_user=ctx["user"],
        data=data,
    )


@router.get(
    "/stats",
    response_model=AppointmentStatsResponse,
    summary="Appointment Statistics",
    description="Returns today's and this week's appointment counts by status.",
)
async def stats(
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_appointment_stats(db, ctx["hospital_id"])


@router.get(
    "/doctor-schedule",
    response_model=DoctorScheduleResponse,
    summary="Doctor Daily Schedule",
    description=(
        "Returns all appointments for a specific doctor on a specific date. "
        "Ordered by scheduled time ascending. "
        "Date defaults to today if not provided."
    ),
)
async def doctor_schedule(
    doctor_id: UUID = Query(..., description="UUID of the doctor"),
    schedule_date: date = Query(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today.",
    ),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    if schedule_date is None:
        schedule_date = date.today()

    return await get_doctor_schedule(
        db=db,
        hospital_id=ctx["hospital_id"],
        doctor_id=doctor_id,
        schedule_date=schedule_date,
    )


@router.get(
    "/",
    response_model=AppointmentListResponse,
    summary="List Appointments",
    description=(
        "Returns a paginated list of appointments. "
        "Supports filtering by doctor, patient, status, type, and date range."
    ),
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    doctor_id: UUID | None = Query(default=None),
    patient_id: UUID | None = Query(default=None),
    status: str | None = Query(
        default=None,
        description="scheduled | confirmed | in_progress | completed | cancelled | no_show",
    ),
    appointment_type: str | None = Query(
        default=None,
        description="consultation | follow_up | procedure | laboratory | imaging | ...",
    ),
    date_from: datetime | None = Query(
        default=None,
        description="Filter appointments from this datetime (ISO 8601)",
    ),
    date_to: datetime | None = Query(
        default=None,
        description="Filter appointments up to this datetime (ISO 8601)",
    ),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_appointments(
        db=db,
        hospital_id=ctx["hospital_id"],
        page=page,
        per_page=per_page,
        doctor_id=doctor_id,
        patient_id=patient_id,
        status_filter=status,
        appointment_type_filter=appointment_type,
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/{appointment_id}",
    response_model=AppointmentDetailResponse,
    summary="Get Appointment",
    description="Returns the full details of a single appointment.",
)
async def get_one(
    appointment_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_appointment(db, ctx["hospital_id"], appointment_id)


@router.put(
    "/{appointment_id}",
    response_model=AppointmentDetailResponse,
    summary="Update Appointment",
    description=(
        "Updates scheduling details of an appointment. "
        "Runs conflict detection if rescheduling. "
        "Cannot update completed, cancelled, or no_show appointments."
    ),
)
async def update(
    appointment_id: UUID,
    data: AppointmentUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await update_appointment(
        db=db,
        hospital_id=ctx["hospital_id"],
        appointment_id=appointment_id,
        data=data,
        requesting_user=ctx["user"],
    )


@router.patch(
    "/{appointment_id}/status",
    response_model=AppointmentDetailResponse,
    summary="Update Appointment Status",
    description=(
        "Transitions an appointment to a new status. "
        "Only valid transitions are accepted. "
        "Cancellation requires a reason. "
        "scheduled → confirmed | cancelled. "
        "confirmed → in_progress | cancelled | no_show. "
        "in_progress → completed | cancelled."
    ),
)
async def update_status(
    appointment_id: UUID,
    data: AppointmentStatusUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await update_appointment_status(
        db=db,
        hospital_id=ctx["hospital_id"],
        appointment_id=appointment_id,
        data=data,
        requesting_user=ctx["user"],
    )