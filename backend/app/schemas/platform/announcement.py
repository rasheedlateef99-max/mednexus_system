"""
Announcement Schemas
--------------------------
Request/response shapes for the announcement broadcast system.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime


class AnnouncementCreateRequest(BaseModel):
    """Platform admin: create a new announcement (draft by default)."""
    title: str = Field(..., min_length=3, max_length=255)
    body: str = Field(..., min_length=1)
    severity: str = Field(default="info")
    target_type: str = Field(default="all_hospitals")
    target_value: str | None = Field(
        None,
        description="Required for specific_plan/specific_hospital — the Plan or Hospital UUID as a string",
    )
    expires_at: datetime | None = None
    publish_immediately: bool = Field(
        default=False,
        description="If true, publishes right away instead of saving as draft",
    )

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v):
        allowed = {"info", "warning", "critical"}
        if v not in allowed:
            raise ValueError(f"severity must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("target_type")
    @classmethod
    def validate_target_type(cls, v):
        allowed = {"all_hospitals", "specific_plan", "specific_hospital"}
        if v not in allowed:
            raise ValueError(f"target_type must be one of: {', '.join(sorted(allowed))}")
        return v


class AnnouncementUpdateRequest(BaseModel):
    """Platform admin: edit a draft announcement. All fields optional."""
    title: str | None = Field(None, min_length=3, max_length=255)
    body: str | None = Field(None, min_length=1)
    severity: str | None = None
    target_type: str | None = None
    target_value: str | None = None
    expires_at: datetime | None = None


class AnnouncementResponse(BaseModel):
    id: UUID
    title: str
    body: str
    severity: str
    target_type: str
    target_value: str | None
    is_published: bool
    published_at: datetime | None
    created_by: UUID
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    created_by_name: str = ""
    read_count: int = 0

    model_config = {"from_attributes": True}


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementResponse]
    total: int
    page: int
    per_page: int
    pages: int


class HospitalAnnouncementResponse(BaseModel):
    """What a hospital sees — strips admin-only fields like created_by."""
    id: UUID
    title: str
    body: str
    severity: str
    published_at: datetime | None
    expires_at: datetime | None
    is_read: bool = False


class HospitalAnnouncementListResponse(BaseModel):
    items: list[HospitalAnnouncementResponse]
    unread_count: int