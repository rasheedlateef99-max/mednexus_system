"""
Medical Record Model (Tenant Database)
-----------------------------------------
Represents a clinical encounter document produced during a patient visit.

Structure follows the SOAP note format used in clinical medicine worldwide:
  S — Subjective:  Chief complaint, history of presenting illness,
                   review of systems (patient's perspective)
  O — Objective:   Physical examination, vital signs, investigation results
                   (clinician's observations)
  A — Assessment:  Diagnosis, differential diagnoses, ICD-10 codes
  P — Plan:        Treatment plan, medications, referrals, follow-up

Record Lifecycle:
  draft       → Doctor is actively writing the record
  finalised   → Doctor has completed and signed the record
  amended     → Record was edited after finalisation (within 24h window)
  locked      → Record is frozen. No further edits permitted.

Amendment Policy:
  - A finalised record can be amended within 24 hours of finalisation
  - The amendment reason and editor are logged in amendment_history
  - After 24 hours, the record status moves to 'locked' automatically
    (enforced in the service layer on every read/write attempt)
  - Locked records are read-only permanently

One-to-One with Appointments:
  Each appointment can have at most one medical record.
  This is enforced in the service layer (not at DB level) to allow
  records to exist without an associated appointment (e.g., imported
  historical records from a previous system).

Vital Signs:
  Stored as a JSON column for flexibility. The vital signs schema
  is validated in the application layer. Storing as JSON allows
  different types of encounters to record different measurements
  without requiring schema changes.
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Boolean, DateTime,
    Enum as SAEnum, func, UUID, JSON
)
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


class RecordStatus(str, enum.Enum):
    """
    Lifecycle status of a medical record.

    draft     → Being written. Editable without restriction.
    finalised → Doctor has signed. Editable within 24h amendment window.
    amended   → Was edited after finalisation. Amendment logged.
    locked    → Frozen. Read-only permanently. No exceptions.
    """
    DRAFT = "draft"
    FINALISED = "finalised"
    AMENDED = "amended"
    LOCKED = "locked"


class MedicalRecord(TenantBase):
    """
    Medical record table in the tenant database.

    One row per clinical encounter.
    hospital_id is the tenant isolation key.
    """
    __tablename__ = "medical_records"

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
    # Patient this record belongs to
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Doctor who authored this record
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Appointment this record documents (nullable — allows historical imports)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # ── S: Subjective (Patient's Perspective) ─────────────────────────────────

    # Why the patient came — the presenting problem in the patient's words
    chief_complaint: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Detailed narrative of the presenting illness:
    # onset, duration, character, severity, aggravating/relieving factors
    history_of_presenting_illness: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Relevant past medical history
    past_medical_history: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Family history relevant to current presentation
    family_history: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Social history (occupation, smoking, alcohol, living situation)
    social_history: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Current medications (can differ from patient baseline if recently changed)
    current_medications_note: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Allergies noted during this encounter
    allergies_noted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── O: Objective (Clinician's Observations) ────────────────────────────────

    # Physical examination findings — narrative text
    # e.g. "Chest: Clear to auscultation bilaterally. No wheezes."
    examination_findings: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Vital signs stored as JSON for flexibility
    # Schema (all optional):
    # {
    #   "blood_pressure_systolic": 120,    (mmHg)
    #   "blood_pressure_diastolic": 80,    (mmHg)
    #   "pulse_rate": 72,                  (beats per minute)
    #   "respiratory_rate": 16,            (breaths per minute)
    #   "temperature": 36.8,              (Celsius)
    #   "oxygen_saturation": 98,          (percentage)
    #   "weight": 75.5,                   (kg)
    #   "height": 175.0,                  (cm)
    #   "bmi": 24.7,                      (calculated)
    #   "blood_glucose": 5.4,             (mmol/L)
    #   "pain_score": 3                   (0-10 scale)
    # }
    vital_signs: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )

    # Investigation results referenced in this encounter
    # (lab results, imaging reports ordered separately)
    investigation_results: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # ── A: Assessment (Clinical Judgement) ────────────────────────────────────

    # Primary diagnosis — plain text (e.g. "Community-acquired pneumonia")
    diagnosis: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ICD-10 code for the primary diagnosis (e.g. "J18.9")
    # ICD-10 = International Classification of Diseases, 10th revision
    # Used for billing, reporting, and epidemiology
    icd10_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ICD-10 description (populated from code lookup, stored for convenience)
    icd10_description: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # Secondary diagnoses as JSON array
    # [{"diagnosis": "Hypertension", "icd10_code": "I10", "icd10_description": "..."}]
    secondary_diagnoses: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )

    # Clinical reasoning and differential diagnoses (doctor's thinking)
    clinical_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── P: Plan ───────────────────────────────────────────────────────────────

    # Treatment plan — what will be done
    treatment_plan: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Follow-up instructions for the patient
    follow_up_instructions: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Referral details if the patient is being referred to a specialist
    referral_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Sick leave / medical certificate details
    sick_leave_days: Mapped[int | None] = mapped_column(
        nullable=True
    )

    # ── Record Administration ─────────────────────────────────────────────────

    status: Mapped[RecordStatus] = mapped_column(
        SAEnum(RecordStatus),
        nullable=False,
        default=RecordStatus.DRAFT,
        index=True,
    )

    # When the doctor finalised (signed) the record
    finalised_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # After this time the record becomes locked
    # Set to finalised_at + 24 hours when record is finalised
    lock_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Amendment history — JSON array of amendment log entries
    # Each entry: {
    #   "amended_at": "ISO datetime",
    #   "amended_by": "user UUID",
    #   "amended_by_name": "Dr. Name",
    #   "reason": "Correction to diagnosis",
    #   "fields_changed": ["diagnosis", "treatment_plan"]
    # }
    amendment_history: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
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
            f"<MedicalRecord id={self.id} "
            f"patient_id={self.patient_id} "
            f"doctor_id={self.doctor_id} "
            f"status={self.status}>"
        )