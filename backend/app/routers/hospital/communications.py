"""
Internal Communications Router
-------------------------------------
Staff-to-staff messaging within a hospital. Every route scopes to
ctx["hospital_id"] and verifies the requesting user is an active
participant before allowing access to a conversation's contents.

Route order: /direct, /department, /group (specific create actions)
before /{conversation_id} (parameterized).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user
from app.schemas.hospital.communication import (
    StartDirectConversationRequest, StartDepartmentConversationRequest,
    StartGroupConversationRequest, SendMessageRequest,
    ConversationResponse, ConversationListResponse,
)
from app.services.hospital.communication_service import (
    start_direct_conversation, start_department_conversation, start_group_conversation,
    send_message, get_conversation, mark_conversation_read, leave_conversation,
    list_my_conversations,
)

router = APIRouter()


@router.post(
    "/direct",
    response_model=ConversationResponse,
    status_code=201,
    summary="Start or Continue a Direct Conversation",
    description="Sends a message to another staff member. Reuses the existing direct conversation if one already exists.",
)
async def direct(
    data: StartDirectConversationRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await start_direct_conversation(db, ctx["hospital_id"], ctx["user"].id, data)


@router.post(
    "/department",
    response_model=ConversationResponse,
    status_code=201,
    summary="Start Department Conversation",
    description="Creates a group conversation with all current staff in a department.",
)
async def department(
    data: StartDepartmentConversationRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await start_department_conversation(db, ctx["hospital_id"], ctx["user"].id, data)


@router.post(
    "/group",
    response_model=ConversationResponse,
    status_code=201,
    summary="Start Group Conversation",
    description="Creates an ad-hoc group conversation with a specific list of participants.",
)
async def group(
    data: StartGroupConversationRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await start_group_conversation(db, ctx["hospital_id"], ctx["user"].id, data)


@router.get(
    "/",
    response_model=ConversationListResponse,
    summary="My Conversations",
    description="Returns all active (non-archived, non-left) conversations for the current user, most recently active first.",
)
async def list_mine(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_my_conversations(db, ctx["hospital_id"], ctx["user"].id, page, per_page)


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get Conversation",
    description="Returns full conversation detail with participants and message history.",
)
async def get_one(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_conversation(db, ctx["hospital_id"], conversation_id, ctx["user"].id)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationResponse,
    summary="Send Message",
    description="Sends a message to an existing conversation you're a participant in.",
)
async def send(
    conversation_id: UUID,
    data: SendMessageRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await send_message(db, ctx["hospital_id"], conversation_id, ctx["user"].id, data)


@router.post(
    "/{conversation_id}/read",
    summary="Mark Conversation as Read",
    description="Updates your read cursor to now for this conversation.",
)
async def mark_read(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await mark_conversation_read(db, conversation_id, ctx["user"].id)


@router.post(
    "/{conversation_id}/leave",
    summary="Leave Conversation",
    description="Removes you from a group or department conversation. Direct conversations cannot be left.",
)
async def leave(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await leave_conversation(db, conversation_id, ctx["user"].id)