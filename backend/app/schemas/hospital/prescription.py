"""
Prescription Schemas
----------------------
Request and response shapes for prescription management endpoints.

Design decisions:
  - Prescription creation includes items inline (issued together)
  - Items can be added to an existing prescription separately
  - Dispensing is done per-item or for the whole prescription
  - Cancellation requires a reason
  - prescription_number is generated server-side (not sent by client)
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime, date
from typing import Optional


# ── Item Schemas ──────────────────────────────────────────────────────────────

class PrescriptionItemCreate(BaseModel):
    """
    A single drug line submitted when creating a prescription.
    Only drug_name is required — all other fields are clinical detail.
    """
    drug_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Drug name (brand or generic)",
        examples=["Amoxicillin", "Paracetamol 500mg tablets"],
    )
    generic_name: str | None = Field(None, max_length=255)
    strength: str | None = Field(
        None,
        max_length=100,
        description="Strength e.g. '500mg', '250mg/5ml'",
    )
    dosage_form: str | None = Field(
        None,
        max_length=100,
        description="tablet | capsule | syrup | injection | cream | drops",
    )
    dose: str | None = Field(
        None,
        max_length=100,
        description="Dose per administration e.g. '1 tablet', '10ml'",
    )
    frequency: str | None = Field(
        None,
        max_length=100,
        description="How often e.g. 'Once daily', 'TDS', 'PRN'",
    )
    duration: str | None = Field(
        None,
        max_length=100,
        description="How long e.g. '7 days', '2 weeks'",
    )
    route: str | None = Field(
        None,
        max_length=100,
        description="Route of administration e.g. 'Oral', 'IV', 'Topical'",
    )
    instructions: str | None = Field(
        None,
        description="Additional instructions e.g. 'Take with food'",
    )
    quantity_prescribed: int | None = Field(
        None,
        ge=1,
        description="Total quantity to dispense e.g. 21 tablets",
    )
    quantity_unit: str | None = Field(
        None,
        max_length=50,
        description="Unit e.g. 'tablets', 'capsules', 'ml'",
    )


class PrescriptionItemDispenseRequest(BaseModel):
    """
    Body for dispensing a single prescription item.
    Sent by a pharmacist when processing one drug line.
    """
    quantity_dispensed: int = Field(
        ...,
        ge=0,
        description="Quantity actually dispensed",
    )
    status: str = Field(
        ...,
        description="dispensed | not_available | cancelled",
    )
    dispensing_notes: str | None = Field(None, max_length=500)
    substituted: bool = Field(
        default=False,
        description="Was a generic substituted for the prescribed drug?",
    )
    substitution_drug: str | None = Field(
        None,
        max_length=255,
        description="Name of the substituted drug if substituted=True",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        allowed = {"dispensed", "not_available", "cancelled"}
        if v not in allowed:
            raise ValueError(
                f"Item status must be one of: {', '.join(sorted(allowed))}"
            )
        return v


class PrescriptionItemResponse(BaseModel):
    """Full prescription item details."""
    id: UUID
    hospital_id: UUID
    prescription_id: UUID
    drug_name: str
    generic_name: str | None
    strength: str | None
    dosage_form: str | None
    dose: str | None
    frequency: str | None
    duration: str | None
    route: str | None
    instructions: str | None
    quantity_prescribed: int | None
    quantity_unit: str | None
    quantity_dispensed: int
    status: str
    dispensing_notes: str | None
    substituted: bool
    substitution_drug: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Prescription Schemas ──────────────────────────────────────────────────────

class PrescriptionCreateRequest(BaseModel):
    """
    Body for issuing a new prescription.

    patient_id and doctor_id are required.
    At least one item is required — a prescription without drugs is meaningless.
    """
    patient_id: UUID = Field(..., description="Patient receiving the prescription")
    doctor_id: UUID = Field(..., description="Doctor issuing the prescription")
    medical_record_id: UUID | None = Field(
        None,
        description="Medical record this prescription is linked to",
    )
    appointment_id: UUID | None = Field(None)
    pharmacist_notes: str | None = Field(None, max_length=1000)
    diagnosis_note: str | None = Field(
        None,
        max_length=500,
        description="Indication or diagnosis (for pharmacist context)",
    )
    validity_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Days this prescription remains valid",
    )
    items: list[PrescriptionItemCreate] = Field(
        ...,
        min_length=1,
        description="Drug items — at least one required",
    )


class PrescriptionCancelRequest(BaseModel):
    """Body for cancelling a prescription."""
    reason: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Reason for cancellation — required",
    )


class PrescriptionResponse(BaseModel):
    """Full prescription with all items."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    medical_record_id: UUID | None
    appointment_id: UUID | None
    prescription_number: str
    pharmacist_notes: str | None
    diagnosis_note: str | None
    validity_days: int
    expires_on: date | None
    status: str
    dispensed_by: UUID | None
    dispensed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    cancelled_by: UUID | None
    created_at: datetime
    updated_at: datetime

    # Items
    items: list[PrescriptionItemResponse] = []

    # Populated by service layer
    patient_name: str = ""
    doctor_name: str = ""
    dispensed_by_name: str = ""

    model_config = {"from_attributes": True}


class PrescriptionSummary(BaseModel):
    """Compact prescription for list views."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    prescription_number: str
    diagnosis_note: str | None
    status: str
    expires_on: date | None
    item_count: int = 0
    created_at: datetime

    # Populated by service layer
    patient_name: str = ""
    doctor_name: str = ""

    model_config = {"from_attributes": True}


class PrescriptionListResponse(BaseModel):
    """Paginated list of prescription summaries."""
    items: list[PrescriptionSummary]
    total: int
    page: int
    per_page: int
    pages: int


class PatientPrescriptionHistoryResponse(BaseModel):
    """All prescriptions for one patient."""
    patient_id: UUID
    patient_name: str
    total_prescriptions: int
    prescriptions: list[PrescriptionSummary]