"""
Platform Support Ticket Router
------------------------------------
Platform admin view: see and manage ALL tickets across ALL hospitals.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.database import get_platform_db, get_tenant_db
from app.core.dependencies import get_current_platform_admin
from app.models.platform.support_ticket import AuthorType, SupportTicket, TicketStatus
from app.schemas.platform.support_ticket import (
    MessageCreateRequest, TicketStatusUpdateRequest, TicketAssignRequest,
    TicketResponse, TicketListResponse,
)
from app.services.platform.support_ticket_service import (
    add_message, get_ticket, list_tickets, update_ticket_status, assign_ticket,
)
from app.services.hospital.notification_service import (
    create_notification, NotificationEventType,
)

router = APIRouter()


@router.get(
    "/",
    response_model=TicketListResponse,
    summary="List All Tickets",
    description="Returns all support tickets across all hospitals. Filterable by status, category, priority, and hospital.",
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    hospital_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await list_tickets(db, page, per_page, hospital_id, status, category, priority)


@router.get(
    "/{ticket_id}",
    response_model=TicketResponse,
    summary="Get Ticket",
    description="Returns full ticket detail including the complete message thread.",
)
async def get_one(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    admin=Depends(get_current_platform_admin),
):
    return await get_ticket(db, tenant_db, ticket_id)


@router.post(
    "/{ticket_id}/messages",
    response_model=TicketResponse,
    summary="Reply to Ticket",
    description="Platform admin replies to a ticket. Auto-moves status to in_progress if currently open.",
)
async def reply(
    ticket_id: UUID,
    data: MessageCreateRequest,
    db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    admin=Depends(get_current_platform_admin),
):
    result = await add_message(db, tenant_db, ticket_id, admin.id, AuthorType.PLATFORM_ADMIN, data)

    # Notify the hospital user who opened the ticket (M20 hook)
    ticket_result = await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = ticket_result.scalar_one_or_none()
    if ticket:
        await create_notification(
            db=tenant_db,
            hospital_id=ticket.hospital_id,
            recipient_id=ticket.opened_by,
            event_type=NotificationEventType.SUPPORT_TICKET_REPLY,
            title="New Reply on Your Support Ticket",
            body=f"A MedNexus team member has replied to your ticket '{ticket.subject}'.",
            link_path=f"/support-tickets/{ticket_id}",
            source_type="support_ticket",
            source_id=ticket_id,
        )

    return result


@router.patch(
    "/{ticket_id}/status",
    response_model=TicketResponse,
    summary="Update Ticket Status",
    description="Explicitly change ticket status (resolve, close, reopen).",
)
async def change_status(
    ticket_id: UUID,
    data: TicketStatusUpdateRequest,
    db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    admin=Depends(get_current_platform_admin),
):
    result = await update_ticket_status(db, tenant_db, ticket_id, data)

    # Notify the hospital user when ticket is resolved or closed (M20 hook)
    new_status = data.status
    if new_status in ("resolved", "closed"):
        ticket_result = await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
        ticket = ticket_result.scalar_one_or_none()
        if ticket:
            event_type = (
                NotificationEventType.SUPPORT_TICKET_RESOLVED
                if new_status == "resolved"
                else NotificationEventType.SUPPORT_TICKET_CLOSED
            )
            title = (
                "Your Support Ticket Has Been Resolved"
                if new_status == "resolved"
                else "Your Support Ticket Has Been Closed"
            )
            await create_notification(
                db=tenant_db,
                hospital_id=ticket.hospital_id,
                recipient_id=ticket.opened_by,
                event_type=event_type,
                title=title,
                body=f"Your ticket '{ticket.subject}' has been marked as {new_status} by our team.",
                link_path=f"/support-tickets/{ticket_id}",
                source_type="support_ticket",
                source_id=ticket_id,
            )

    return result


@router.patch(
    "/{ticket_id}/assign",
    response_model=TicketResponse,
    summary="Assign Ticket",
    description="Assign this ticket to a specific platform admin.",
)
async def assign(
    ticket_id: UUID,
    data: TicketAssignRequest,
    db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    admin=Depends(get_current_platform_admin),
):
    return await assign_ticket(db, tenant_db, ticket_id, data)