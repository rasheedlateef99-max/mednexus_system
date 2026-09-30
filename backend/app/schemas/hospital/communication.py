"""
Internal Communication Schemas
-------------------------------------
Request/response shapes for staff-to-staff messaging.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime


class StartDirectConversationRequest(BaseModel):
    """Starts (or returns the existing) direct conversation with one other staff member."""
    recipient_id: UUID = Field(..., description="The other staff member's user ID")
    initial_message: str = Field(..., min_length=1)


class StartDepartmentConversationRequest(BaseModel):
    """Creates a department-scoped group conversation, snapshotting current department staff."""
    department_id: UUID
    title: str | None = Field(None, max_length=255)
    initial_message: str = Field(..., min_length=1)


class StartGroupConversationRequest(BaseModel):
    """Creates an ad-hoc group conversation with an explicit participant list."""
    participant_ids: list[UUID] = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=255)
    initial_message: str = Field(..., min_length=1)


class SendMessageRequest(BaseModel):
    body: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    sender_name: str = ""
    body: str
    is_deleted: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ParticipantResponse(BaseModel):
    user_id: UUID
    user_name: str = ""
    joined_at: datetime
    left_at: datetime | None


class ConversationResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    conversation_type: str
    title: str | None
    department_id: UUID | None
    created_by: UUID
    is_archived: bool
    created_at: datetime
    last_message_at: datetime

    participants: list[ParticipantResponse] = []
    messages: list[MessageResponse] = []
    unread_count: int = 0

    model_config = {"from_attributes": True}


class ConversationSummary(BaseModel):
    """Compact conversation for the inbox list view."""
    id: UUID
    conversation_type: str
    title: str | None
    last_message_at: datetime
    last_message_preview: str = ""
    unread_count: int = 0
    participant_names: list[str] = []

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    total: int
    page: int
    per_page: int
    pages: int