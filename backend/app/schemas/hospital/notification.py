"""
Notification Schemas
--------------------------
Request/response shapes for the in-app notification system.
"""

from pydantic import BaseModel
from uuid import UUID
from datetime import datetime


class NotificationResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    recipient_id: UUID
    event_type: str
    title: str
    body: str
    link_path: str | None
    is_read: bool
    source_type: str | None
    source_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int
    page: int
    per_page: int
    pages: int