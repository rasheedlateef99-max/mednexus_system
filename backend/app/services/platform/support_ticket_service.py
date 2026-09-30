"""
Support Ticket Service
----------------------------
Business logic for ticket creation, threaded replies, status transitions,
and assignment.

Status Transition Rules:
  Hospital opens ticket            → status: open
  Admin sends first reply          → status: in_progress (auto)
  Admin marks resolved             → status: resolved (explicit action)
  Hospital replies to resolved/closed → status: in_progress (auto-reopen)
  Admin closes                     → status: closed (explicit action)

Cross-Database Name Resolution:
  Hospital names come from Hospital (platform DB, same session).
  Hospital user names (opened_by, and any author_type=hospital_user
  message author) require a SEPARATE tenant DB session, since
  HospitalUser lives in mednexus, not mednexus_platform.
  Platform admin names (assigned_to, author_type=platform_admin)
  come from PlatformAdmin, same session as everything else here.
"""

import uuid
import re
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.platform.hospital import Hospital
from app.models.platform.platform_admin import PlatformAdmin
from app.models.platform.support_ticket import (
    SupportTicket, TicketMessage, TicketStatus, TicketCategory, TicketPriority, AuthorType,
)
from app.schemas.platform.support_ticket import (
    TicketCreateRequest, MessageCreateRequest, TicketStatusUpdateRequest, TicketAssignRequest,
    TicketResponse, TicketSummary, TicketListResponse, TicketMessageResponse,
)
from app.core.exceptions import NotFoundException, ValidationException, ForbiddenException


async def _generate_ticket_number(db: AsyncSession) -> str:
    """Sequential TKT-XXXXX, global across all hospitals (platform-owned resource)."""
    result = await db.execute(
        select(SupportTicket.ticket_number).order_by(SupportTicket.created_at.desc())
    )
    existing = result.scalars().all()
    max_number = 0
    for number_str in existing:
        match = re.search(r"TKT-(\d+)", number_str)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"TKT-{max_number + 1:05d}"


async def _get_hospital_name(db: AsyncSession, hospital_id: uuid.UUID) -> str:
    result = await db.execute(select(Hospital.name).where(Hospital.id == hospital_id))
    row = result.first()
    return row[0] if row else ""


async def _get_admin_name(db: AsyncSession, admin_id: uuid.UUID | None) -> str:
    if not admin_id:
        return ""
    result = await db.execute(select(PlatformAdmin.full_name).where(PlatformAdmin.id == admin_id))
    row = result.first()
    return row[0] if row else ""


async def _get_hospital_user_name(tenant_db: AsyncSession, user_id: uuid.UUID) -> str:
    """Requires a tenant DB session — HospitalUser doesn't live in the platform DB."""
    from app.models.tenant.hospital_user import HospitalUser
    result = await tenant_db.execute(
        select(HospitalUser.full_name).where(HospitalUser.id == user_id)
    )
    row = result.first()
    return row[0] if row else ""


async def _get_messages(
    db: AsyncSession, tenant_db: AsyncSession, ticket_id: uuid.UUID,
) -> list[TicketMessageResponse]:
    result = await db.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id)
        .order_by(TicketMessage.created_at.asc())
    )
    messages = result.scalars().all()
    responses = []
    for m in messages:
        r = TicketMessageResponse.model_validate(m)
        if m.author_type == AuthorType.HOSPITAL_USER:
            r.author_name = await _get_hospital_user_name(tenant_db, m.author_id)
        else:
            r.author_name = await _get_admin_name(db, m.author_id)
        responses.append(r)
    return responses


async def _build_response(
    db: AsyncSession, tenant_db: AsyncSession, ticket: SupportTicket,
) -> TicketResponse:
    response = TicketResponse.model_validate(ticket)
    response.hospital_name = await _get_hospital_name(db, ticket.hospital_id)
    response.opened_by_name = await _get_hospital_user_name(tenant_db, ticket.opened_by)
    response.assigned_to_name = await _get_admin_name(db, ticket.assigned_to)
    response.messages = await _get_messages(db, tenant_db, ticket.id)
    return response


async def _build_summary(db: AsyncSession, ticket: SupportTicket) -> TicketSummary:
    summary = TicketSummary.model_validate(ticket)
    summary.hospital_name = await _get_hospital_name(db, ticket.hospital_id)
    count_result = await db.execute(
        select(func.count(TicketMessage.id)).where(TicketMessage.ticket_id == ticket.id)
    )
    summary.message_count = count_result.scalar() or 0
    return summary


# ── Core Service Functions ────────────────────────────────────────────────────

async def create_ticket(
    db: AsyncSession,
    tenant_db: AsyncSession,
    hospital_id: uuid.UUID,
    opening_user_id: uuid.UUID,
    data: TicketCreateRequest,
) -> TicketResponse:
    """Hospital-side: opens a new ticket with its initial message."""
    ticket_number = await _generate_ticket_number(db)

    ticket = SupportTicket(
        hospital_id=hospital_id,
        opened_by=opening_user_id,
        ticket_number=ticket_number,
        subject=data.subject,
        category=TicketCategory(data.category),
        priority=TicketPriority(data.priority),
        status=TicketStatus.OPEN,
    )
    db.add(ticket)
    await db.flush()

    message = TicketMessage(
        ticket_id=ticket.id,
        author_id=opening_user_id,
        author_type=AuthorType.HOSPITAL_USER,
        body=data.message,
    )
    db.add(message)
    await db.commit()
    await db.refresh(ticket)

    return await _build_response(db, tenant_db, ticket)


async def add_message(
    db: AsyncSession,
    tenant_db: AsyncSession,
    ticket_id: uuid.UUID,
    author_id: uuid.UUID,
    author_type: AuthorType,
    data: MessageCreateRequest,
    hospital_id_scope: uuid.UUID | None = None,
) -> TicketResponse:
    """
    Adds a reply to a ticket. Used by BOTH the hospital router and the
    platform router, distinguished by author_type.

    hospital_id_scope: if provided (hospital-side calls), enforces that
    the ticket actually belongs to that hospital — prevents a hospital
    user from posting into another hospital's ticket by guessing an ID.
    """
    query = select(SupportTicket).where(SupportTicket.id == ticket_id)
    if hospital_id_scope:
        query = query.where(SupportTicket.hospital_id == hospital_id_scope)

    result = await db.execute(query)
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise NotFoundException("Ticket")

    if ticket.status == TicketStatus.CLOSED and author_type == AuthorType.HOSPITAL_USER:
        # Hospital replying to a closed ticket reopens it
        ticket.status = TicketStatus.IN_PROGRESS
    elif ticket.status == TicketStatus.RESOLVED and author_type == AuthorType.HOSPITAL_USER:
        ticket.status = TicketStatus.IN_PROGRESS
    elif ticket.status == TicketStatus.OPEN and author_type == AuthorType.PLATFORM_ADMIN:
        ticket.status = TicketStatus.IN_PROGRESS

    message = TicketMessage(
        ticket_id=ticket.id,
        author_id=author_id,
        author_type=author_type,
        body=data.body,
    )
    db.add(message)
    await db.commit()
    await db.refresh(ticket)

    return await _build_response(db, tenant_db, ticket)


async def get_ticket(
    db: AsyncSession,
    tenant_db: AsyncSession,
    ticket_id: uuid.UUID,
    hospital_id_scope: uuid.UUID | None = None,
) -> TicketResponse:
    query = select(SupportTicket).where(SupportTicket.id == ticket_id)
    if hospital_id_scope:
        query = query.where(SupportTicket.hospital_id == hospital_id_scope)

    result = await db.execute(query)
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise NotFoundException("Ticket")

    return await _build_response(db, tenant_db, ticket)


async def list_tickets(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 20,
    hospital_id_scope: uuid.UUID | None = None,
    status_filter: str | None = None,
    category_filter: str | None = None,
    priority_filter: str | None = None,
) -> TicketListResponse:
    query = select(SupportTicket).order_by(SupportTicket.updated_at.desc())

    if hospital_id_scope:
        query = query.where(SupportTicket.hospital_id == hospital_id_scope)
    if status_filter:
        try:
            query = query.where(SupportTicket.status == TicketStatus(status_filter))
        except ValueError:
            pass
    if category_filter:
        try:
            query = query.where(SupportTicket.category == TicketCategory(category_filter))
        except ValueError:
            pass
    if priority_filter:
        try:
            query = query.where(SupportTicket.priority == TicketPriority(priority_filter))
        except ValueError:
            pass

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    tickets = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    items = [await _build_summary(db, t) for t in tickets]

    return TicketListResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)


async def update_ticket_status(
    db: AsyncSession,
    tenant_db: AsyncSession,
    ticket_id: uuid.UUID,
    data: TicketStatusUpdateRequest,
) -> TicketResponse:
    """Platform admin only — explicit status change."""
    result = await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise NotFoundException("Ticket")

    new_status = TicketStatus(data.status)
    now = datetime.now(timezone.utc)

    ticket.status = new_status
    if new_status == TicketStatus.RESOLVED:
        ticket.resolved_at = now
    elif new_status == TicketStatus.CLOSED:
        ticket.closed_at = now

    await db.commit()
    await db.refresh(ticket)

    return await _build_response(db, tenant_db, ticket)


async def assign_ticket(
    db: AsyncSession,
    tenant_db: AsyncSession,
    ticket_id: uuid.UUID,
    data: TicketAssignRequest,
) -> TicketResponse:
    """Platform admin only — assign ticket to a specific admin."""
    result = await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise NotFoundException("Ticket")

    admin_result = await db.execute(select(PlatformAdmin).where(PlatformAdmin.id == data.admin_id))
    if not admin_result.scalar_one_or_none():
        raise NotFoundException("Platform admin")

    ticket.assigned_to = data.admin_id
    await db.commit()
    await db.refresh(ticket)

    return await _build_response(db, tenant_db, ticket)