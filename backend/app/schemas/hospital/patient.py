"""
Patient Schemas
-----------------
Request and response shapes for patient management endpoints.

Design decisions explained:
  - Registration vs Update are separate schemas because registration
    requires first_name and last_name but updates make everything optional
  - age is computed in the response (not stored) using the model property
  - allergies, chronic_conditions, current_medications are lists of strings
  - All PHI fields are included in responses — access control is enforced
    at the router level via dependency injection
"""

from pydantic import BaseModel, Field, EmailStr, field_validator
from uuid import UUID
from datetime import datetime, date
from typing import Optional


# ── Enums (mirrored from model for schema validation) ─────────────────────────

VALID_GENDERS = {"male", "female", "other", "prefer_not_to_say"}
VALID_BLOOD_TYPES = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown"}
VALID_MARITAL_STATUSES = {
    "single", "married", "divorced", "widowed", "separated", "other"
}


# ── Registration Request ──────────────────────────────────────────────────────

class PatientRegisterRequest(BaseModel):
    """
    Body for registering a new patient.

    Only first_name and last_name are required.
    All other fields are collected progressively as they become available.
    This reflects real clinical workflow — receptionists often register
    patients with minimal information and fill in details later.
    """

    # ── Required fields ───────────────────────────────────────────────────────
    first_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Patient's first name",
        examples=["John"],
    )
    last_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Patient's last name / surname",
        examples=["Adeyemi"],
    )

    # ── Personal information ──────────────────────────────────────────────────
    middle_name: str | None = Field(None, max_length=100)
    date_of_birth: date | None = Field(
        None,
        description="Format: YYYY-MM-DD",
        examples=["1985-03-22"],
    )
    gender: str | None = Field(
        None,
        description="male | female | other | prefer_not_to_say",
    )
    marital_status: str | None = Field(
        None,
        description="single | married | divorced | widowed | separated | other",
    )
    national_id: str | None = Field(
        None,
        max_length=100,
        description="National ID, passport, or other government-issued ID",
    )

    # ── Contact ───────────────────────────────────────────────────────────────
    phone_primary: str | None = Field(None, max_length=50)
    phone_secondary: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=255)

    # ── Address ───────────────────────────────────────────────────────────────
    address_line1: str | None = Field(None, max_length=255)
    address_line2: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    country: str | None = Field(None, max_length=100)

    # ── Medical background ────────────────────────────────────────────────────
    blood_type: str | None = Field(
        None,
        description="A+ | A- | B+ | B- | AB+ | AB- | O+ | O- | unknown",
    )
    allergies: list[str] = Field(
        default_factory=list,
        description="List of known allergies",
        examples=[["Penicillin", "Latex"]],
    )
    chronic_conditions: list[str] = Field(
        default_factory=list,
        description="List of known chronic conditions",
        examples=[["Type 2 Diabetes", "Hypertension"]],
    )
    current_medications: list[str] = Field(
        default_factory=list,
        description="List of current medications",
        examples=[["Metformin 500mg"]],
    )
    medical_notes: str | None = Field(None, max_length=2000)

    # ── Emergency contact ─────────────────────────────────────────────────────
    emergency_contact_name: str | None = Field(None, max_length=255)
    emergency_contact_relationship: str | None = Field(None, max_length=100)
    emergency_contact_phone: str | None = Field(None, max_length=50)

    # ── Insurance ─────────────────────────────────────────────────────────────
    insurance_provider: str | None = Field(None, max_length=255)
    insurance_number: str | None = Field(None, max_length=100)

    # ── Occupation ────────────────────────────────────────────────────────────
    occupation: str | None = Field(None, max_length=255)
    employer: str | None = Field(None, max_length=255)

    # ── Assignment ────────────────────────────────────────────────────────────
    assigned_doctor_id: UUID | None = Field(
        None,
        description="UUID of the doctor assigned to this patient",
    )

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v):
        if v is not None and v not in VALID_GENDERS:
            raise ValueError(
                f"Invalid gender. Must be one of: {', '.join(sorted(VALID_GENDERS))}"
            )
        return v

    @field_validator("blood_type")
    @classmethod
    def validate_blood_type(cls, v):
        if v is not None and v not in VALID_BLOOD_TYPES:
            raise ValueError(
                f"Invalid blood type. Must be one of: {', '.join(sorted(VALID_BLOOD_TYPES))}"
            )
        return v

    @field_validator("marital_status")
    @classmethod
    def validate_marital_status(cls, v):
        if v is not None and v not in VALID_MARITAL_STATUSES:
            raise ValueError(
                f"Invalid marital status. "
                f"Must be one of: {', '.join(sorted(VALID_MARITAL_STATUSES))}"
            )
        return v

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, v):
        if v is not None and v > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return v


# ── Update Request ────────────────────────────────────────────────────────────

class PatientUpdateRequest(BaseModel):
    """
    Body for updating an existing patient record.

    Every field is optional — send only what needs to change.
    Fields not included in the request are left unchanged.

    This is the standard "partial update" pattern. It is safer than
    requiring the full object because:
    1. The caller only sends what they changed
    2. No risk of accidentally overwriting fields with stale data
    3. Smaller payloads over the network
    """
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    middle_name: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    marital_status: str | None = None
    national_id: str | None = Field(None, max_length=100)
    phone_primary: str | None = Field(None, max_length=50)
    phone_secondary: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=255)
    address_line1: str | None = Field(None, max_length=255)
    address_line2: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    country: str | None = Field(None, max_length=100)
    blood_type: str | None = None
    allergies: list[str] | None = None
    chronic_conditions: list[str] | None = None
    current_medications: list[str] | None = None
    medical_notes: str | None = None
    emergency_contact_name: str | None = Field(None, max_length=255)
    emergency_contact_relationship: str | None = Field(None, max_length=100)
    emergency_contact_phone: str | None = Field(None, max_length=50)
    insurance_provider: str | None = Field(None, max_length=255)
    insurance_number: str | None = Field(None, max_length=100)
    occupation: str | None = Field(None, max_length=255)
    employer: str | None = Field(None, max_length=255)
    assigned_doctor_id: UUID | None = None

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v):
        if v is not None and v not in VALID_GENDERS:
            raise ValueError(f"Invalid gender. Must be one of: {', '.join(sorted(VALID_GENDERS))}")
        return v

    @field_validator("blood_type")
    @classmethod
    def validate_blood_type(cls, v):
        if v is not None and v not in VALID_BLOOD_TYPES:
            raise ValueError(f"Invalid blood type. Must be one of: {', '.join(sorted(VALID_BLOOD_TYPES))}")
        return v

    @field_validator("marital_status")
    @classmethod
    def validate_marital_status(cls, v):
        if v is not None and v not in VALID_MARITAL_STATUSES:
            raise ValueError(f"Invalid marital status. Must be one of: {', '.join(sorted(VALID_MARITAL_STATUSES))}")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, v):
        if v is not None and v > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return v


# ── Response Schemas ──────────────────────────────────────────────────────────

class PatientSummary(BaseModel):
    """
    Compact patient representation for list views.

    Used in search results and patient lists where showing
    every field would be too much data.
    """
    id: UUID
    hospital_id: UUID
    patient_number: str
    first_name: str
    last_name: str
    middle_name: str | None
    date_of_birth: date | None
    gender: str | None
    phone_primary: str | None
    email: str | None
    blood_type: str | None
    is_active: bool
    created_at: datetime

    # Computed fields — populated by the service layer
    age: int | None = None
    full_name: str = ""

    model_config = {"from_attributes": True}


class PatientDetailResponse(BaseModel):
    """
    Full patient record with all fields.

    Returned when viewing a single patient's complete profile.
    """
    id: UUID
    hospital_id: UUID
    patient_number: str

    # Personal
    first_name: str
    last_name: str
    middle_name: str | None
    date_of_birth: date | None
    gender: str | None
    marital_status: str | None
    national_id: str | None

    # Contact
    phone_primary: str | None
    phone_secondary: str | None
    email: str | None

    # Address
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    country: str | None

    # Medical
    blood_type: str | None
    allergies: list[str]
    chronic_conditions: list[str]
    current_medications: list[str]
    medical_notes: str | None

    # Emergency contact
    emergency_contact_name: str | None
    emergency_contact_relationship: str | None
    emergency_contact_phone: str | None

    # Insurance
    insurance_provider: str | None
    insurance_number: str | None

    # Occupation
    occupation: str | None
    employer: str | None

    # Assignment
    registered_by: UUID | None
    assigned_doctor_id: UUID | None

    # Status
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Computed fields
    age: int | None = None
    full_name: str = ""

    model_config = {"from_attributes": True}


class PatientListResponse(BaseModel):
    """Paginated list of patient summaries."""
    items: list[PatientSummary]
    total: int
    page: int
    per_page: int
    pages: int


class PatientSearchResponse(BaseModel):
    """Search results — same structure as list but semantically distinct."""
    items: list[PatientSummary]
    total: int
    query: str
    page: int
    per_page: int