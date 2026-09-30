"""
Hospital Registration Schemas
--------------------------------
Pydantic models for the hospital onboarding flow.

Covers:
  - Plan selection
  - Hospital information submission
  - Registration status responses
"""

from pydantic import BaseModel, Field, HttpUrl
from uuid import UUID
from datetime import datetime
from typing import Optional


# ── Plan Selection ────────────────────────────────────────────────────────────

class PlanSummary(BaseModel):
    """Summary of a subscription plan shown during plan selection."""
    id: UUID
    name: str
    code: str
    description: str | None
    price_monthly: float
    price_annual: float
    currency: str
    trial_days: int
    has_trial: bool
    max_staff_users: int
    max_patient_records: int
    features: dict

    model_config = {"from_attributes": True}


# ── Hospital Information Form ─────────────────────────────────────────────────

class HospitalRegistrationRequest(BaseModel):
    """
    Data submitted by the hospital admin during registration.

    This creates the Hospital record in the platform database
    with status: pending_review.
    """

    # ── Basic Information ─────────────────────────────────────────────────────
    name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Full legal name of the hospital or healthcare organization",
        examples=["City General Hospital"],
    )

    description: str | None = Field(
        None,
        max_length=1000,
        description="Brief description of the hospital and its services",
    )

    # ── Location ──────────────────────────────────────────────────────────────
    country: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Country where the hospital is located",
        examples=["Nigeria", "Ghana", "United Kingdom"],
    )

    state: str | None = Field(
        None,
        max_length=100,
        description="State, province, or region",
    )

    city: str | None = Field(
        None,
        max_length=100,
        description="City or town",
    )

    address: str | None = Field(
        None,
        max_length=500,
        description="Full street address",
    )

    # ── Contact ───────────────────────────────────────────────────────────────
    phone: str | None = Field(
        None,
        max_length=50,
        description="Primary contact phone number",
    )

    email: str | None = Field(
        None,
        max_length=255,
        description="Official hospital email address",
    )

    website: str | None = Field(
        None,
        max_length=255,
        description="Hospital website URL",
    )

    # ── Verification ──────────────────────────────────────────────────────────
    registration_number: str | None = Field(
        None,
        max_length=100,
        description="Government or regulatory registration number",
    )

    # Selected plan ID from the plans table
    plan_id: UUID = Field(
        ...,
        description="ID of the subscription plan selected during onboarding",
    )

    # Billing preference
    billing_cycle: str = Field(
        default="trial",
        description="trial | monthly | annual",
        pattern=r"^(trial|monthly|annual)$",
    )


# ── Registration Responses ────────────────────────────────────────────────────

class HospitalRegistrationResponse(BaseModel):
    """
    Response after successful hospital registration submission.
    The hospital is now pending review by the platform admin.
    """
    hospital_id: UUID
    hospital_name: str
    status: str
    message: str
    estimated_review_time: str = "1-2 business days"


class HospitalStatusResponse(BaseModel):
    """
    Current status of a hospital's application.
    Hospital admins can poll this to track their approval.
    """
    hospital_id: UUID
    hospital_name: str
    status: str
    submitted_at: datetime
    approved_at: datetime | None
    review_notes: str | None
    subscription_status: str | None

    model_config = {"from_attributes": True}


# ── Platform Admin: Hospital Management ───────────────────────────────────────

class HospitalApprovalRequest(BaseModel):
    """Body for approving or rejecting a hospital application."""
    action: str = Field(
        ...,
        pattern=r"^(approve|reject)$",
        description="approve or reject",
    )
    notes: str | None = Field(
        None,
        max_length=1000,
        description="Review notes visible to the hospital admin",
    )


class HospitalSuspensionRequest(BaseModel):
    """Body for suspending a hospital account."""
    reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Reason for suspension (visible internally)",
    )


class HospitalListItem(BaseModel):
    """Summary of a hospital for list views in the platform admin dashboard."""
    id: UUID
    name: str
    country: str | None
    status: str
    created_at: datetime
    approved_at: datetime | None
    subscription_status: str | None

    model_config = {"from_attributes": True}


class PaginatedHospitalList(BaseModel):
    """Paginated list of hospitals for the platform admin dashboard."""
    items: list[HospitalListItem]
    total: int
    page: int
    per_page: int
    pages: int