"""
Support Ticket Schemas
----------------------------
Request/response shapes for the support ticket system.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime


class TicketCreateRequest(BaseModel):
    """Hospital-side: opening a new ticket."""
    subject: str = Field(..., min_length=3, max_length=255)
    category: str = Field(default="other")
    priority: str = Field(default="normal")
    message: str = Field(..., min_length=1, description="The initial message body")

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        allowed = {"billing", "technical", "account", "feature_request", "bug_report", "other"}
        if v not in allowed:
            raise ValueError(f"category must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v):
        allowed = {"low", "normal", "high", "urgent"}
        if v not in allowed:
            raise ValueError(f"priority must be one of: {', '.join(sorted(allowed))}")
        return v


class MessageCreateRequest(BaseModel):
    """Body for replying to an existing ticket — either side can use this."""
    body: str = Field(..., min_length=1)


class TicketStatusUpdateRequest(BaseModel):
    """Platform admin: change ticket status directly (resolve/close/reopen)."""
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        allowed = {"open", "in_progress", "resolved", "closed"}
        if v not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return v


class TicketAssignRequest(BaseModel):
    """Platform admin: assign ticket to a specific admin."""
    admin_id: UUID


class TicketMessageResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    author_id: UUID
    author_type: str
    author_name: str = ""
    body: str
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    opened_by: UUID
    ticket_number: str
    subject: str
    category: str
    priority: str
    status: str
    assigned_to: UUID | None
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    hospital_name: str = ""
    opened_by_name: str = ""
    assigned_to_name: str = ""
    messages: list[TicketMessageResponse] = []

    model_config = {"from_attributes": True}


class TicketSummary(BaseModel):
    id: UUID
    hospital_id: UUID
    ticket_number: str
    subject: str
    category: str
    priority: str
    status: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    hospital_name: str = ""

    model_config = {"from_attributes": True}


class TicketListResponse(BaseModel):
    items: list[TicketSummary]
    total: int
    page: int
    per_page: int
    pages: int