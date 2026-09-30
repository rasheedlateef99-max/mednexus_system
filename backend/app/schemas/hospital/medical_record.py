"""
Medical Record Schemas
------------------------
Request and response shapes for medical record endpoints.

Design decisions:
  - All clinical fields are optional in both create and update schemas.
    Doctors save progressively as they work. A draft with only a chief
    complaint is valid.
  - VitalSigns is a nested Pydantic model for structured validation.
  - The finalise endpoint has its own request (just a confirmation).
  - Responses include computed fields: is_locked, can_amend.
  - Amendment requires an explicit reason — no silent edits.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime
from typing import Optional


# ── Vital Signs ───────────────────────────────────────────────────────────────

class VitalSigns(BaseModel):
    """
    Structured vital signs measurement.

    All fields are optional — different encounters measure different things.
    A post-operative check might only record pulse and BP.
    A routine check might record everything.
    """
    blood_pressure_systolic: int | None = Field(
        None, ge=40, le=300, description="Systolic BP in mmHg"
    )
    blood_pressure_diastolic: int | None = Field(
        None, ge=20, le=200, description="Diastolic BP in mmHg"
    )
    pulse_rate: int | None = Field(
        None, ge=20, le=300, description="Pulse rate in beats per minute"
    )
    respiratory_rate: int | None = Field(
        None, ge=4, le=60, description="Respiratory rate in breaths per minute"
    )
    temperature: float | None = Field(
        None, ge=25.0, le=45.0, description="Temperature in Celsius"
    )
    oxygen_saturation: float | None = Field(
        None, ge=50.0, le=100.0, description="SpO2 percentage"
    )
    weight: float | None = Field(
        None, ge=0.1, le=700.0, description="Weight in kilograms"
    )
    height: float | None = Field(
        None, ge=20.0, le=300.0, description="Height in centimetres"
    )
    bmi: float | None = Field(
        None, ge=5.0, le=100.0, description="Body Mass Index (can be auto-calculated)"
    )
    blood_glucose: float | None = Field(
        None, ge=0.1, le=100.0, description="Blood glucose in mmol/L"
    )
    pain_score: int | None = Field(
        None, ge=0, le=10, description="Pain score 0-10"
    )


# ── Secondary Diagnosis ───────────────────────────────────────────────────────

class SecondaryDiagnosis(BaseModel):
    """A secondary diagnosis entry in the record."""
    diagnosis: str = Field(..., min_length=1, max_length=500)
    icd10_code: str | None = Field(None, max_length=20)
    icd10_description: str | None = Field(None, max_length=255)


# ── Create Request ────────────────────────────────────────────────────────────

class MedicalRecordCreateRequest(BaseModel):
    """
    Body for creating a new medical record.

    patient_id and doctor_id are required.
    appointment_id is recommended but optional (for historical imports).
    All clinical fields are optional — records are saved progressively.
    """

    # ── Required references ───────────────────────────────────────────────────
    patient_id: UUID = Field(..., description="Patient this record belongs to")
    doctor_id: UUID = Field(..., description="Doctor authoring this record")
    appointment_id: UUID | None = Field(
        None,
        description="Appointment this record documents (recommended)",
    )

    # ── Subjective ────────────────────────────────────────────────────────────
    chief_complaint: str | None = Field(None, max_length=1000)
    history_of_presenting_illness: str | None = None
    past_medical_history: str | None = None
    family_history: str | None = None
    social_history: str | None = None
    current_medications_note: str | None = None
    allergies_noted: str | None = None

    # ── Objective ─────────────────────────────────────────────────────────────
    examination_findings: str | None = None
    vital_signs: VitalSigns = Field(
        default_factory=VitalSigns,
        description="Vital signs measurements",
    )
    investigation_results: str | None = None

    # ── Assessment ────────────────────────────────────────────────────────────
    diagnosis: str | None = Field(None, max_length=500)
    icd10_code: str | None = Field(
        None,
        max_length=20,
        description="ICD-10 code e.g. J18.9",
    )
    icd10_description: str | None = Field(None, max_length=255)
    secondary_diagnoses: list[SecondaryDiagnosis] = Field(default_factory=list)
    clinical_notes: str | None = None

    # ── Plan ──────────────────────────────────────────────────────────────────
    treatment_plan: str | None = None
    follow_up_instructions: str | None = None
    referral_details: str | None = None
    sick_leave_days: int | None = Field(None, ge=0, le=365)


# ── Update Request ────────────────────────────────────────────────────────────

class MedicalRecordUpdateRequest(BaseModel):
    """
    Body for updating a draft medical record.

    All fields optional — send only what changed.
    Only valid on records in 'draft' or within the 24h amendment window.
    """
    chief_complaint: str | None = Field(None, max_length=1000)
    history_of_presenting_illness: str | None = None
    past_medical_history: str | None = None
    family_history: str | None = None
    social_history: str | None = None
    current_medications_note: str | None = None
    allergies_noted: str | None = None
    examination_findings: str | None = None
    vital_signs: VitalSigns | None = None
    investigation_results: str | None = None
    diagnosis: str | None = Field(None, max_length=500)
    icd10_code: str | None = Field(None, max_length=20)
    icd10_description: str | None = Field(None, max_length=255)
    secondary_diagnoses: list[SecondaryDiagnosis] | None = None
    clinical_notes: str | None = None
    treatment_plan: str | None = None
    follow_up_instructions: str | None = None
    referral_details: str | None = None
    sick_leave_days: int | None = Field(None, ge=0, le=365)


# ── Amendment Request ─────────────────────────────────────────────────────────

class MedicalRecordAmendRequest(BaseModel):
    """
    Body for amending a finalised medical record.

    Amendments require an explicit reason.
    They are only permitted within 24 hours of finalisation.
    The reason is logged permanently in amendment_history.
    """
    amendment_reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description=(
            "Reason for amending this record. "
            "Required and logged permanently."
        ),
    )

    # Same clinical fields as update
    chief_complaint: str | None = Field(None, max_length=1000)
    history_of_presenting_illness: str | None = None
    past_medical_history: str | None = None
    family_history: str | None = None
    social_history: str | None = None
    current_medications_note: str | None = None
    allergies_noted: str | None = None
    examination_findings: str | None = None
    vital_signs: VitalSigns | None = None
    investigation_results: str | None = None
    diagnosis: str | None = Field(None, max_length=500)
    icd10_code: str | None = Field(None, max_length=20)
    icd10_description: str | None = Field(None, max_length=255)
    secondary_diagnoses: list[SecondaryDiagnosis] | None = None
    clinical_notes: str | None = None
    treatment_plan: str | None = None
    follow_up_instructions: str | None = None
    referral_details: str | None = None
    sick_leave_days: int | None = Field(None, ge=0, le=365)


# ── Response Schemas ──────────────────────────────────────────────────────────

class MedicalRecordSummary(BaseModel):
    """Compact record for list views and patient history timelines."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    appointment_id: UUID | None
    chief_complaint: str | None
    diagnosis: str | None
    icd10_code: str | None
    status: str
    finalised_at: datetime | None
    created_at: datetime

    # Populated by service layer
    patient_name: str = ""
    doctor_name: str = ""

    # Computed
    is_locked: bool = False
    can_amend: bool = False

    model_config = {"from_attributes": True}


class MedicalRecordDetailResponse(BaseModel):
    """Full medical record with all clinical fields."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    appointment_id: UUID | None

    # Subjective
    chief_complaint: str | None
    history_of_presenting_illness: str | None
    past_medical_history: str | None
    family_history: str | None
    social_history: str | None
    current_medications_note: str | None
    allergies_noted: str | None

    # Objective
    examination_findings: str | None
    vital_signs: dict
    investigation_results: str | None

    # Assessment
    diagnosis: str | None
    icd10_code: str | None
    icd10_description: str | None
    secondary_diagnoses: list
    clinical_notes: str | None

    # Plan
    treatment_plan: str | None
    follow_up_instructions: str | None
    referral_details: str | None
    sick_leave_days: int | None

    # Administration
    status: str
    finalised_at: datetime | None
    lock_after: datetime | None
    amendment_history: list
    created_at: datetime
    updated_at: datetime

    # Populated by service layer
    patient_name: str = ""
    doctor_name: str = ""

    # Computed
    is_locked: bool = False
    can_amend: bool = False

    model_config = {"from_attributes": True}


class MedicalRecordListResponse(BaseModel):
    """Paginated list of medical record summaries."""
    items: list[MedicalRecordSummary]
    total: int
    page: int
    per_page: int
    pages: int


class PatientMedicalHistoryResponse(BaseModel):
    """
    Complete medical history for one patient.

    Returned chronologically (most recent first) so clinicians
    immediately see the most current clinical picture.
    """
    patient_id: UUID
    patient_name: str
    total_records: int
    records: list[MedicalRecordSummary]