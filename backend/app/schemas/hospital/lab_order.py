"""
Laboratory Order Schemas
---------------------------
Request and response shapes for lab order management.
Mirrors the prescription schema pattern: header + items.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime


# ── Item Schemas ──────────────────────────────────────────────────────────────

class LabOrderItemCreate(BaseModel):
    """A single test requested when creating a lab order."""
    test_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        examples=["Full Blood Count", "Liver Function Test"],
    )
    test_category: str | None = Field(
        None,
        max_length=100,
        description="Haematology | Biochemistry | Microbiology | Immunology | etc.",
    )


class LabResultEntryRequest(BaseModel):
    """
    Body for entering a result for a single lab order item.
    Submitted by a lab scientist.
    """
    result_value: str | None = Field(
        None,
        max_length=500,
        description="Text result e.g. 'Negative', '5.4'",
    )
    numeric_value: float | None = Field(
        None,
        description="Numeric result for quantitative tests",
    )
    unit: str | None = Field(None, max_length=50)
    reference_range: str | None = Field(
        None,
        max_length=100,
        description="Display range e.g. '3.5 - 5.5 mmol/L'",
    )
    reference_low: float | None = Field(
        None,
        description="Numeric lower bound for automatic abnormal detection",
    )
    reference_high: float | None = Field(
        None,
        description="Numeric upper bound for automatic abnormal detection",
    )
    is_abnormal: bool | None = Field(
        None,
        description=(
            "Manually flag as abnormal. If not provided and numeric bounds "
            "are given, this is computed automatically."
        ),
    )
    is_critical: bool = Field(
        default=False,
        description="Flag for results requiring immediate doctor notification",
    )
    lab_notes: str | None = Field(None, max_length=1000)

    @field_validator("reference_high")
    @classmethod
    def validate_range(cls, v, info):
        ref_low = info.data.get("reference_low")
        if v is not None and ref_low is not None and v < ref_low:
            raise ValueError("reference_high must be greater than reference_low")
        return v


class LabOrderItemResponse(BaseModel):
    """Full lab order item details."""
    id: UUID
    hospital_id: UUID
    lab_order_id: UUID
    test_name: str
    test_category: str | None
    result_value: str | None
    numeric_value: float | None
    unit: str | None
    reference_range: str | None
    reference_low: float | None
    reference_high: float | None
    is_abnormal: bool
    is_critical: bool
    lab_notes: str | None
    status: str
    resulted_by: UUID | None
    resulted_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Lab Order Schemas ─────────────────────────────────────────────────────────

class LabOrderCreateRequest(BaseModel):
    """Body for ordering laboratory tests."""
    patient_id: UUID = Field(..., description="Patient for whom tests are ordered")
    doctor_id: UUID = Field(..., description="Ordering doctor")
    medical_record_id: UUID | None = None
    appointment_id: UUID | None = None
    clinical_indication: str | None = Field(
        None,
        max_length=1000,
        description="Why these tests are being ordered",
    )
    priority: str = Field(
        default="routine",
        description="routine | urgent | stat",
    )
    items: list[LabOrderItemCreate] = Field(
        ...,
        min_length=1,
        description="Tests requested — at least one required",
    )

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v):
        allowed = {"routine", "urgent", "stat"}
        if v not in allowed:
            raise ValueError(f"Priority must be one of: {', '.join(sorted(allowed))}")
        return v


class SpecimenCollectionRequest(BaseModel):
    """Body for recording specimen collection."""
    specimen_type: str = Field(
        ...,
        max_length=100,
        description="Venous blood | Urine | Sputum | Stool | Swab | etc.",
    )


class LabOrderReviewRequest(BaseModel):
    """Body for a doctor reviewing and signing off on completed results."""
    doctor_comments: str | None = Field(None, max_length=1000)


class LabOrderCancelRequest(BaseModel):
    """Body for cancelling a lab order."""
    reason: str = Field(..., min_length=5, max_length=500)


class LabOrderResponse(BaseModel):
    """Full lab order with all test items."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    medical_record_id: UUID | None
    appointment_id: UUID | None
    order_number: str
    clinical_indication: str | None
    priority: str
    status: str
    specimen_collected_at: datetime | None
    specimen_collected_by: UUID | None
    specimen_type: str | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    doctor_comments: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime
    updated_at: datetime

    items: list[LabOrderItemResponse] = []

    patient_name: str = ""
    doctor_name: str = ""

    model_config = {"from_attributes": True}


class LabOrderSummary(BaseModel):
    """Compact lab order for list views."""
    id: UUID
    hospital_id: UUID
    patient_id: UUID
    doctor_id: UUID
    order_number: str
    priority: str
    status: str
    item_count: int = 0
    has_abnormal_results: bool = False
    has_critical_results: bool = False
    created_at: datetime

    patient_name: str = ""
    doctor_name: str = ""

    model_config = {"from_attributes": True}


class LabOrderListResponse(BaseModel):
    items: list[LabOrderSummary]
    total: int
    page: int
    per_page: int
    pages: int


class PatientLabHistoryResponse(BaseModel):
    patient_id: UUID
    patient_name: str
    total_orders: int
    orders: list[LabOrderSummary]