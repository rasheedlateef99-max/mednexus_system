"""
Staff Management Schemas
--------------------------
Request and response shapes for staff management endpoints.
"""

from pydantic import BaseModel, EmailStr, Field
from uuid import UUID
from datetime import datetime


# ── Invitation ────────────────────────────────────────────────────────────────

class StaffInviteRequest(BaseModel):
    """
    Body for inviting a new staff member.

    The admin provides basic info. The invited person sets their
    own password when they accept the invitation.
    """
    full_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Full name of the staff member",
    )
    email: EmailStr = Field(
        ...,
        description="Email address — used for login and the invitation email",
    )
    role: str = Field(
        ...,
        description=(
            "Staff role: doctor | nurse | pharmacist | "
            "lab_scientist | receptionist | accountant | hr_manager"
        ),
    )
    department_id: UUID | None = Field(
        None,
        description="Department to assign this staff member to",
    )
    specialization: str | None = Field(
        None,
        max_length=255,
        description="Medical specialization (for doctors and specialists)",
    )
    license_number: str | None = Field(
        None,
        max_length=100,
        description="Professional license number",
    )


class InvitationAcceptRequest(BaseModel):
    """
    Body for accepting a staff invitation.

    The invited staff member clicks the link in their email,
    lands on the frontend, and submits this form to complete setup.
    """
    invitation_token: str = Field(
        ...,
        description="Token from the invitation email link",
    )
    password: str = Field(
        ...,
        min_length=8,
        description="Password the staff member chooses for their account",
    )
    confirm_password: str = Field(
        ...,
        min_length=8,
        description="Must match password",
    )


# ── Staff Profile Updates ─────────────────────────────────────────────────────

class StaffUpdateRequest(BaseModel):
    """Body for updating a staff member's profile. All fields optional."""
    full_name: str | None = Field(None, min_length=2, max_length=255)
    role: str | None = None
    department_id: UUID | None = None
    specialization: str | None = Field(None, max_length=255)
    license_number: str | None = Field(None, max_length=100)


# ── Response Schemas ──────────────────────────────────────────────────────────

class StaffMemberResponse(BaseModel):
    """Full staff member profile."""
    id: UUID
    hospital_id: UUID
    full_name: str
    email: str
    role: str
    status: str
    department_id: UUID | None
    specialization: str | None
    license_number: str | None
    is_owner: bool
    avatar_url: str | None
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class StaffListResponse(BaseModel):
    """Paginated list of staff members."""
    items: list[StaffMemberResponse]
    total: int
    page: int
    per_page: int


class InvitationResponse(BaseModel):
    """Response after successfully sending a staff invitation."""
    message: str
    staff_id: UUID
    email: str
    role: str
    invitation_sent: bool