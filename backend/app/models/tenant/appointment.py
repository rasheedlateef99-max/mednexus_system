"""
Appointment Model (Tenant Database)
--------------------------------------
Represents a scheduled clinical appointment between a patient and a doctor.

Relationships:
  - Belongs to one hospital   (hospital_id — tenant key)
  - Belongs to one patient    (patient_id)
  - Belongs to one doctor     (doctor_id — references hospital_users)
  - Optionally scoped to a department (department_id)

Status Lifecycle:
  scheduled   → The appointment has been booked
  confirmed   → The appointment has been confirmed (patient notified)
  in_progress → The doctor has started the consultation
  completed   → The consultation is finished
  cancelled   → The appointment was cancelled before it started
  no_show     → The patient did not attend

Why duration_minutes instead of end_time:
  Duration is the input datum. End time is derived (start + duration).
  Storing derived data creates the risk of inconsistency.
  The service layer computes end time when needed for conflict checking.

Conflict Detection Logic (enforced in service layer):
  A new appointment conflicts with an existing one if:
    new_start < existing_end AND new_end > existing_start
  This is the standard interval overlap formula.
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Integer, DateTime,
    Enum as SAEnum, func, UUID, Boolean
)
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


class AppointmentStatus(str, enum.Enum):
    """
    The lifecycle status of an appointment.

    Transitions:
      scheduled   → confirmed, cancelled
      confirmed   → in_progress, cancelled, no_show
      in_progress → completed, cancelled
      completed   → (terminal — no further transitions)
      cancelled   → (terminal — no further transitions)
      no_show     → (terminal — no further transitions)
    """
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class AppointmentType(str, enum.Enum):
    """
    The nature of the appointment.
    Used for scheduling, reporting, and billing categorisation.
    """
    CONSULTATION = "consultation"       # General doctor visit
    FOLLOW_UP = "follow_up"             # Follow-up on previous visit
    PROCEDURE = "procedure"             # Minor procedure or treatment
    LABORATORY = "laboratory"           # Lab test appointment
    IMAGING = "imaging"                 # X-ray, scan, etc.
    VACCINATION = "vaccination"         # Immunisation
    EMERGENCY = "emergency"             # Urgent/walk-in
    TELEMEDICINE = "telemedicine"       # Remote consultation (future)
    OTHER = "other"


class Appointment(TenantBase):
    """
    Appointment table in the tenant database.

    One row per appointment.
    hospital_id is the tenant isolation key — every query must filter by it.
    """
    __tablename__ = "appointments"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Tenant Key ────────────────────────────────────────────────────────────
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # ── Core References ───────────────────────────────────────────────────────
    # Patient attending the appointment
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Doctor conducting the appointment
    # References hospital_users.id — must be a user with role=doctor
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Department where the appointment takes place (optional)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # ── Scheduling ────────────────────────────────────────────────────────────
    # Start time stored in UTC
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # Duration in minutes — end time is computed as scheduled_at + duration
    # Default: 30 minutes
    duration_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
    )

    # ── Classification ────────────────────────────────────────────────────────
    appointment_type: Mapped[AppointmentType] = mapped_column(
        SAEnum(AppointmentType),
        nullable=False,
        default=AppointmentType.CONSULTATION,
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(AppointmentStatus),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
        index=True,
    )

    # ── Clinical Notes ────────────────────────────────────────────────────────
    # Reason for the visit — filled by the booking staff
    chief_complaint: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Internal notes added during or after the appointment
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ── Cancellation ─────────────────────────────────────────────────────────
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Who cancelled — references hospital_users.id
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # ── Completion Tracking ───────────────────────────────────────────────────
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── Booking & Update Metadata ─────────────────────────────────────────────
    # Who booked this appointment — references hospital_users.id
    booked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Who last updated this appointment record — references hospital_users.id
    # Populated by the service layer on reschedule, status change, etc.
    # Closes the audit gap documented in ADR-006.
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Whether the patient was sent a reminder
    reminder_sent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Appointment id={self.id} "
            f"patient_id={self.patient_id} "
            f"doctor_id={self.doctor_id} "
            f"scheduled_at={self.scheduled_at} "
            f"status={self.status}>"
        )