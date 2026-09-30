"""
Appointment Schemas
---------------------
Request and response shapes for appointment management endpoints.

Design decisions:
  - scheduled_at accepts ISO 8601 datetime strings with timezone
  - duration_minutes defaults to 30 if not provided
  - Status transitions are validated in the service layer, not here
  - doctor_id is required — appointments must have an assigned doctor
  - patient_id is required — walk-ins are registered as patients first
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime
from typing import Optional


# ── Valid Values ──────────────────────────────────────────────────────────────

VALID_APPOINTMENT_TYPES = {
    "consultation", "follow_up", "procedure", "laboratory",
    "imaging", "vaccination", "emergency", "telemedicine", "other"
}

VALID_STATUSES = {
    "scheduled", "confirmed", "in_progress", "completed", "cancelled", "no_show"
}

# Valid status transitions — maps current status to allowed next statuses
# This is also enforced in the service layer
STATUS_TRANSITIONS: dict[str, set[str]] = {
    "scheduled":   {"confirmed", "cancelled"},
    "confirmed":   {"in_progress", "cancelled", "no_show"},
    "in_progress": {"completed", "cancelled"},
    "completed":   set(),   # Terminal state
    "cancelled":   set(),   # Terminal state
    "no_show":     set(),   # Terminal state
}


# ── Request Schemas ───────────────────────────────────────────────────────────

class AppointmentBookRequest(BaseModel):
    """
    Body for booking a new appointment.

    Required: patient_id, doctor_id, scheduled_at
    Optional: everything else has sensible defaults
    """
    patient_id: UUID = Field(
        ...,
        description="UUID of the patient",
    )
    doctor_id: UUID = Field(
        ...,
        description="UUID of the doctor (must be a hospital_user with role=doctor)",
    )
    department_id: UUID | None = Field(
        None,
        description="UUID of the department (optional)",
    )
    scheduled_at: datetime = Field(
        ...,
        description=(
            "Appointment start time in ISO 8601 format with timezone. "
            "Example: 2024-03-15T09:30:00+01:00"
        ),
    )
    duration_minutes: int = Field(
        default=30,
        ge=5,
        le=480,
        description="Duration in minutes. Minimum 5, maximum 480 (8 hours).",
    )
    appointment_type: str = Field(
        default="consultation",
        description=(
            "consultation | follow_up | procedure | laboratory | "
            "imaging | vaccination | emergency | telemedicine | other"
        ),
    )
    chief_complaint: str | None = Field(
        None,
        max_length=1000,
        description="Reason for the visit",
    )
    notes: str | None = Field(
        None,
        max_length=2000,
        description="Additional notes",
    )

    @field_validator("appointment_type")
    @classmethod
    def validate_type(cls, v):
        if v not in VALID_APPOINTMENT_TYPES:
            raise ValueError(
                f"Invalid appointment type. "
                f"Must be one of: {', '.join(sorted(VALID_APPOINTMENT_TYPES))}"
            )
        return v

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(cls, v):
        """Appointments cannot be booked in the past."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        # Make v timezone-aware if it isn't already
        if v.tzinfo is None:
            from datetime import timezone
            v = v.replace(tzinfo=timezone.utc)
        if v < now:
            raise ValueError("Appointment cannot be scheduled in the past")
        return v


class AppointmentUpdateRequest(BaseModel):
    """
    Body for updating an existing appointment.

    Can only update scheduling details — not status.
    Status changes go through dedicated endpoints.
    All fields are optional.
    """
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(None, ge=5, le=480)
    department_id: UUID | None = None
    appointment_type: str | None = None
    chief_complaint: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=2000)
    doctor_id: UUID | None = None

    @field_validator("appointment_type")
    @classmethod
    def validate_type(cls, v):
        if v is not None and v not in VALID_APPOINTMENT_TYPES:
            raise ValueError(
                f"Invalid appointment type. "
                f"Must be one of: {', '.join(sorted(VALID_APPOINTMENT_TYPES))}"
            )
        return v


class AppointmentStatusUpdateRequest(BaseModel):
    """
    Body for changing an appointment's status.

    Separate from the main update because status transitions
    have strict rules and some require additional data
    (cancellation requires a reason).
    """
    status: str = Field(
        ...,
        description=(
            "New status. Must be a valid transition from the current status. "
            "scheduled → confirmed | cancelled. "
            "confirmed → in_progress | cancelled | no_show. "
            "in_progress → completed | cancelled."
        ),
    )
    cancellation_reason: str | None = Field(
        None,
        max_length=500,
        description="Required when status is 'cancelled'",
    )
    notes: str | None = Field(
        None,
        max_length=2000,
        description="Optional notes to add when updating status",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status. "
                f"Must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        return v


# ── Response Schemas ──────────────────────────────────────────────────────────

class AppointmentSummary(BaseModel):
    """
    Compact appointment for list views and calendar displays.
    """
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    department_id: UUID | None
    scheduled_at: datetime
    duration_minutes: int
    appointment_type: str
    status: str
    chief_complaint: str | None
    booked_by: UUID | None
    created_at: datetime

    # Populated by service layer from related records
    patient_name: str = ""
    doctor_name: str = ""
    department_name: str | None = None

    model_config = {"from_attributes": True}


class AppointmentDetailResponse(BaseModel):
    """
    Full appointment details including all tracking fields.
    """
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    department_id: UUID | None
    scheduled_at: datetime
    duration_minutes: int
    appointment_type: str
    status: str
    chief_complaint: str | None
    notes: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    cancelled_by: UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    booked_by: UUID | None
    reminder_sent: bool
    created_at: datetime
    updated_at: datetime

    # Computed end time
    end_time: datetime | None = None

    # Populated by service layer
    patient_name: str = ""
    doctor_name: str = ""
    department_name: str | None = None

    model_config = {"from_attributes": True}


class AppointmentListResponse(BaseModel):
    """Paginated list of appointment summaries."""
    items: list[AppointmentSummary]
    total: int
    page: int
    per_page: int
    pages: int


class DoctorScheduleResponse(BaseModel):
    """
    A doctor's appointments for a specific day.
    Ordered by scheduled_at ascending (earliest first).
    """
    doctor_id: UUID
    doctor_name: str
    date: str                           # YYYY-MM-DD
    appointments: list[AppointmentSummary]
    total_appointments: int


class AppointmentStatsResponse(BaseModel):
    """Summary statistics for the hospital dashboard."""
    total_today: int
    scheduled_today: int
    confirmed_today: int
    in_progress_today: int
    completed_today: int
    cancelled_today: int
    no_show_today: int
    total_this_week: int
    total_this_month: int