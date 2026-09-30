"""
Hospital Support Ticket Router
------------------------------------
Hospital-side view: create and view ONLY this hospital's own tickets.
Every query is scoped to ctx["hospital_id"] from the JWT — a hospital
user can never see or post into another hospital's ticket.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db, get_platform_db
from app.core.dependencies import get_current_hospital_user
from app.models.platform.support_ticket import AuthorType
from app.schemas.platform.support_ticket import (
    TicketCreateRequest, MessageCreateRequest, TicketResponse, TicketListResponse,
)
from app.services.platform.support_ticket_service import (
    create_ticket, add_message, get_ticket, list_tickets,
)

router = APIRouter()


@router.post(
    "/",
    response_model=TicketResponse,
    status_code=201,
    summary="Open Support Ticket",
    description="Opens a new support ticket with an initial message. Any active hospital staff can open a ticket.",
)
async def create(
    data: TicketCreateRequest,
    platform_db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await create_ticket(platform_db, tenant_db, ctx["hospital_id"], ctx["user"].id, data)


@router.get(
    "/",
    response_model=TicketListResponse,
    summary="List My Hospital's Tickets",
    description="Returns all tickets opened by this hospital, scoped automatically.",
)
async def list_mine(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    platform_db: AsyncSession = Depends(get_platform_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_tickets(platform_db, page, per_page, ctx["hospital_id"], status)


@router.get(
    "/{ticket_id}",
    response_model=TicketResponse,
    summary="Get Ticket",
    description="Returns ticket detail. Scoped — only accessible if it belongs to your hospital.",
)
async def get_one(
    ticket_id: UUID,
    platform_db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_ticket(platform_db, tenant_db, ticket_id, ctx["hospital_id"])


@router.post(
    "/{ticket_id}/messages",
    response_model=TicketResponse,
    summary="Reply to Ticket",
    description="Reply to your own ticket. Reopens it automatically if it was resolved or closed.",
)
async def reply(
    ticket_id: UUID,
    data: MessageCreateRequest,
    platform_db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await add_message(
        platform_db, tenant_db, ticket_id, ctx["user"].id,
        AuthorType.HOSPITAL_USER, data, hospital_id_scope=ctx["hospital_id"],
    )