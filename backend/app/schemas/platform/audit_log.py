"""
Audit Log Schemas
------------------------
Unified response shape for the cross-table, cross-database audit feed.
Every source table contributes rows normalized into this one shape —
see audit_log_service.py for how each source is mapped.
"""

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class AuditLogEntry(BaseModel):
    """
    One normalized audit event, regardless of source table.

    actor_id is nullable — some source tables never captured WHO performed
    an action, only THAT it happened and WHEN. See service docstring.
    """
    source_table: str = Field(..., description="Which table this event was derived from")
    action: str = Field(..., description="Human-readable action label, e.g. 'hospital_approved'")
    actor_id: UUID | None
    actor_name: str = ""
    actor_type: str = Field(..., description="platform_admin | hospital_user | unknown")
    hospital_id: UUID | None
    hospital_name: str = ""
    entity_type: str
    entity_id: UUID
    occurred_at: datetime
    detail: str | None = Field(None, description="Optional human-readable extra context")


class AuditLogListResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    page: int
    per_page: int
    pages: int


class AuditLogFilterParams(BaseModel):
    """Not used as a request body — documents the query params the router accepts."""
    hospital_id: UUID | None = None
    source_table: str | None = None
    actor_id: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None