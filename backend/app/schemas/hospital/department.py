"""
Department Schemas
--------------------
Request and response shapes for department management endpoints.
"""

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


# ── Request Schemas ───────────────────────────────────────────────────────────

class DepartmentCreateRequest(BaseModel):
    """Body for creating a new department."""
    name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Department name",
        examples=["Cardiology", "Emergency", "Pharmacy"],
    )
    code: str | None = Field(
        None,
        max_length=20,
        description="Short internal code",
        examples=["CARDIO", "EMRG", "PHARM"],
    )
    description: str | None = Field(
        None,
        max_length=500,
        description="Brief description of the department",
    )
    head_of_department_id: UUID | None = Field(
        None,
        description="UUID of the staff member who leads this department",
    )


class DepartmentUpdateRequest(BaseModel):
    """Body for updating an existing department. All fields optional."""
    name: str | None = Field(None, min_length=2, max_length=255)
    code: str | None = Field(None, max_length=20)
    description: str | None = Field(None, max_length=500)
    head_of_department_id: UUID | None = None
    is_active: bool | None = None


# ── Response Schemas ──────────────────────────────────────────────────────────

class DepartmentResponse(BaseModel):
    """Full department details."""
    id: UUID
    hospital_id: UUID
    name: str
    code: str | None
    description: str | None
    head_of_department_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DepartmentListResponse(BaseModel):
    """Paginated list of departments."""
    items: list[DepartmentResponse]
    total: int
    page: int
    per_page: int