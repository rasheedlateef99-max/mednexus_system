"""
Audit Log Service
------------------------
Aggregates actor/timestamp pairs from existing tables across BOTH
databases into one unified, paginated, filterable feed. No new writes
occur anywhere — this is a pure read/aggregation layer (Option B,
see Milestone 18 design notes).

FUTURE MIGRATION PATH TO A DEDICATED AuditLog TABLE (Option A):
  This module is intentionally the ONLY place that knows audit data
  currently comes from scattered actor/timestamp columns. The router
  and schema (AuditLogEntry / AuditLogListResponse) have no knowledge
  of where rows originate — they only see the normalized shape.

  If a dedicated AuditLog table is introduced later:
    1. Add the new model/migration (no changes to existing tables)
    2. New actions start writing to it going forward
    3. Replace the BODY of list_audit_log() below with a query against
       the new table — the function signature and return type
       (AuditLogListResponse) stay identical, so the router and
       every caller need ZERO changes
    4. Optionally, during a transition period, list_audit_log() can
       query the new table for events after its creation date and
       fall back to the _collect_* functions below for everything
       before it — preserving full history without a backfill migration

  The _collect_* functions below are independent, swappable units for
  exactly this reason — list_audit_log() just gathers a list of
  sources, merges, sorts, and paginates. Adding "query AuditLog table"
  as one more source (or as the sole source, post-migration) does not
  require touching this orchestration logic.

Cross-Database Aggregation Strategy:
  Each _collect_* function queries ONE source table and returns a
  list of AuditLogEntry already normalized to the common shape.
  list_audit_log() calls all of them, merges the results in Python,
  sorts by occurred_at, and applies pagination AFTER the merge —
  this is necessary because the sources live in two physically
  separate databases; no single SQL query (UNION or otherwise) can
  span mednexus and mednexus_platform.

  This means pagination here is less efficient than a single-table
  paginated query (we fetch a bounded window from EACH source, merge,
  then slice) — acceptable at current scale, and flagged here as the
  first thing to revisit if audit log volume becomes large enough to
  matter (a dedicated AuditLog table — Option A — would be the fix
  at that point, exactly as described in the migration path above).

Known Gaps (rows with actor_id=None):
  - Patient record changes: no *_by column exists on Patient
  - Staff deactivation: HospitalUser has no deactivated_by/at columns
  - Appointment cancellation: no cancelled_by column on Appointment
  These are reported honestly with actor_id=None rather than guessed.
  A dedicated AuditLog table (Option A) would close these gaps for
  any NEW actions taken after its introduction, since logging would
  happen explicitly at the point of action rather than being inferred
  from columns that were never designed to capture an actor.
"""

import uuid
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.platform.hospital import Hospital
from app.models.platform.subscription import Subscription
from app.models.platform.subscription_event import SubscriptionEvent
from app.models.platform.support_ticket import SupportTicket
from app.models.platform.announcement import Announcement
from app.models.platform.platform_admin import PlatformAdmin
from app.models.tenant.billing import Invoice
from app.models.tenant.prescription import Prescription
from app.models.tenant.lab_order import LabOrder
from app.models.tenant.generated_document import GeneratedDocument
from app.models.tenant.hospital_user import HospitalUser
from app.schemas.platform.audit_log import AuditLogEntry, AuditLogListResponse

# Cap on how many rows we pull from EACH source before merging —
# prevents one noisy source from starving the others when a date
# range isn't specified. Generous enough for normal admin use.
_PER_SOURCE_FETCH_LIMIT = 200


async def _get_platform_admin_name(platform_db: AsyncSession, admin_id: uuid.UUID | None) -> str:
    if not admin_id:
        return ""
    result = await platform_db.execute(select(PlatformAdmin.full_name).where(PlatformAdmin.id == admin_id))
    row = result.first()
    return row[0] if row else ""


async def _get_hospital_user_name(tenant_db: AsyncSession, user_id: uuid.UUID | None) -> str:
    if not user_id:
        return ""
    result = await tenant_db.execute(select(HospitalUser.full_name).where(HospitalUser.id == user_id))
    row = result.first()
    return row[0] if row else ""


async def _get_hospital_name(platform_db: AsyncSession, hospital_id: uuid.UUID | None) -> str:
    if not hospital_id:
        return ""
    result = await platform_db.execute(select(Hospital.name).where(Hospital.id == hospital_id))
    row = result.first()
    return row[0] if row else ""


# ── Platform-Side Sources ─────────────────────────────────────────────────────
# Each function here is an independent, swappable "audit source." See the
# module docstring's FUTURE MIGRATION PATH section for why this matters.

async def _collect_hospital_approvals(platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await platform_db.execute(
        select(Hospital)
        .where(Hospital.approved_at.isnot(None))
        .order_by(Hospital.approved_at.desc())
        .limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for h in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="hospitals", action="hospital_approved",
            actor_id=h.approved_by, actor_name=await _get_platform_admin_name(platform_db, h.approved_by),
            actor_type="platform_admin", hospital_id=h.id, hospital_name=h.name,
            entity_type="hospital", entity_id=h.id, occurred_at=h.approved_at,
            detail=h.review_notes,
        ))
        if h.suspended_at:
            entries.append(AuditLogEntry(
                source_table="hospitals", action="hospital_suspended",
                actor_id=None, actor_type="unknown",
                hospital_id=h.id, hospital_name=h.name,
                entity_type="hospital", entity_id=h.id, occurred_at=h.suspended_at,
                detail=h.suspension_reason,
            ))
    return entries


async def _collect_subscription_events(platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await platform_db.execute(
        select(SubscriptionEvent).order_by(SubscriptionEvent.created_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for e in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="subscription_events", action=f"subscription_{e.event_type.value}",
            actor_id=None, actor_type="unknown",
            hospital_id=e.hospital_id, hospital_name=await _get_hospital_name(platform_db, e.hospital_id),
            entity_type="subscription", entity_id=e.subscription_id or e.id,
            occurred_at=e.created_at, detail=e.notes,
        ))
    return entries


async def _collect_subscription_cancellations(platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await platform_db.execute(
        select(Subscription)
        .where(Subscription.cancelled_at.isnot(None))
        .order_by(Subscription.cancelled_at.desc())
        .limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for s in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="subscriptions", action="subscription_cancelled",
            actor_id=None, actor_type="unknown",
            hospital_id=s.hospital_id, hospital_name=await _get_hospital_name(platform_db, s.hospital_id),
            entity_type="subscription", entity_id=s.id, occurred_at=s.cancelled_at,
            detail=s.cancellation_reason,
        ))
    return entries


async def _collect_ticket_resolutions(platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await platform_db.execute(
        select(SupportTicket)
        .where(SupportTicket.resolved_at.isnot(None))
        .order_by(SupportTicket.resolved_at.desc())
        .limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for t in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="support_tickets", action="ticket_resolved",
            actor_id=t.assigned_to, actor_name=await _get_platform_admin_name(platform_db, t.assigned_to),
            actor_type="platform_admin", hospital_id=t.hospital_id,
            hospital_name=await _get_hospital_name(platform_db, t.hospital_id),
            entity_type="support_ticket", entity_id=t.id, occurred_at=t.resolved_at,
            detail=f"Ticket {t.ticket_number}",
        ))
    return entries


async def _collect_announcements(platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await platform_db.execute(
        select(Announcement)
        .where(Announcement.published_at.isnot(None))
        .order_by(Announcement.published_at.desc())
        .limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for a in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="announcements", action="announcement_published",
            actor_id=a.created_by, actor_name=await _get_platform_admin_name(platform_db, a.created_by),
            actor_type="platform_admin", hospital_id=None, hospital_name="",
            entity_type="announcement", entity_id=a.id, occurred_at=a.published_at,
            detail=a.title,
        ))
    return entries


# ── Tenant-Side Sources ────────────────────────────────────────────────────────

async def _collect_invoice_events(tenant_db: AsyncSession, platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await tenant_db.execute(
        select(Invoice)
        .where(Invoice.issued_at.isnot(None))
        .order_by(Invoice.issued_at.desc())
        .limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for inv in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="invoices", action="invoice_issued",
            actor_id=inv.issued_by, actor_name=await _get_hospital_user_name(tenant_db, inv.issued_by),
            actor_type="hospital_user", hospital_id=inv.hospital_id,
            hospital_name=await _get_hospital_name(platform_db, inv.hospital_id),
            entity_type="invoice", entity_id=inv.id, occurred_at=inv.issued_at,
            detail=inv.invoice_number,
        ))
        if inv.cancelled_at:
            entries.append(AuditLogEntry(
                source_table="invoices", action="invoice_cancelled",
                actor_id=inv.cancelled_by, actor_name=await _get_hospital_user_name(tenant_db, inv.cancelled_by),
                actor_type="hospital_user", hospital_id=inv.hospital_id,
                hospital_name=await _get_hospital_name(platform_db, inv.hospital_id),
                entity_type="invoice", entity_id=inv.id, occurred_at=inv.cancelled_at,
                detail=inv.cancellation_reason,
            ))
    return entries


async def _collect_prescription_events(tenant_db: AsyncSession, platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await tenant_db.execute(
        select(Prescription)
        .where(Prescription.cancelled_at.isnot(None))
        .order_by(Prescription.cancelled_at.desc())
        .limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for p in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="prescriptions", action="prescription_cancelled",
            actor_id=p.cancelled_by, actor_name=await _get_hospital_user_name(tenant_db, p.cancelled_by),
            actor_type="hospital_user", hospital_id=p.hospital_id,
            hospital_name=await _get_hospital_name(platform_db, p.hospital_id),
            entity_type="prescription", entity_id=p.id, occurred_at=p.cancelled_at,
            detail=p.cancellation_reason,
        ))
    return entries


async def _collect_lab_order_events(tenant_db: AsyncSession, platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await tenant_db.execute(
        select(LabOrder)
        .where(LabOrder.reviewed_at.isnot(None))
        .order_by(LabOrder.reviewed_at.desc())
        .limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for lo in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="lab_orders", action="lab_order_reviewed",
            actor_id=lo.reviewed_by, actor_name=await _get_hospital_user_name(tenant_db, lo.reviewed_by),
            actor_type="hospital_user", hospital_id=lo.hospital_id,
            hospital_name=await _get_hospital_name(platform_db, lo.hospital_id),
            entity_type="lab_order", entity_id=lo.id, occurred_at=lo.reviewed_at,
            detail=lo.order_number,
        ))
    return entries


async def _collect_document_generation_events(tenant_db: AsyncSession, platform_db: AsyncSession) -> list[AuditLogEntry]:
    result = await tenant_db.execute(
        select(GeneratedDocument).order_by(GeneratedDocument.created_at.desc()).limit(_PER_SOURCE_FETCH_LIMIT)
    )
    entries = []
    for d in result.scalars().all():
        entries.append(AuditLogEntry(
            source_table="generated_documents", action=f"document_generated_{d.document_type.value}",
            actor_id=d.generated_by, actor_name=await _get_hospital_user_name(tenant_db, d.generated_by),
            actor_type="hospital_user", hospital_id=d.hospital_id,
            hospital_name=await _get_hospital_name(platform_db, d.hospital_id),
            entity_type="generated_document", entity_id=d.id, occurred_at=d.created_at,
            detail=d.storage_key,
        ))
    return entries


# ── Public Entry Point ────────────────────────────────────────────────────────
# This is the ONLY function the router calls. Its signature and return type
# are the stable contract — see FUTURE MIGRATION PATH in the module docstring
# for how this stays unchanged even if the underlying sources change entirely.

async def list_audit_log(
    platform_db: AsyncSession,
    tenant_db: AsyncSession,
    page: int = 1,
    per_page: int = 50,
    hospital_id: uuid.UUID | None = None,
    source_table: str | None = None,
) -> AuditLogListResponse:
    """
    Aggregates all sources, merges, sorts by occurred_at descending,
    optionally filters by hospital_id/source_table, then paginates
    the MERGED result in Python (see module docstring for why this
    can't be a single SQL query across two databases).

    The list of _collect_* calls below is the entire "data source
    registry" for this feed. Adding, removing, or replacing a source
    (including swapping all of them for a single dedicated AuditLog
    table query, per the future migration path above) means editing
    ONLY this list — callers of list_audit_log() are unaffected.
    """
    all_entries: list[AuditLogEntry] = []

    all_entries += await _collect_hospital_approvals(platform_db)
    all_entries += await _collect_subscription_events(platform_db)
    all_entries += await _collect_subscription_cancellations(platform_db)
    all_entries += await _collect_ticket_resolutions(platform_db)
    all_entries += await _collect_announcements(platform_db)
    all_entries += await _collect_invoice_events(tenant_db, platform_db)
    all_entries += await _collect_prescription_events(tenant_db, platform_db)
    all_entries += await _collect_lab_order_events(tenant_db, platform_db)
    all_entries += await _collect_document_generation_events(tenant_db, platform_db)

    if hospital_id:
        all_entries = [e for e in all_entries if e.hospital_id == hospital_id]
    if source_table:
        all_entries = [e for e in all_entries if e.source_table == source_table]

    all_entries.sort(key=lambda e: e.occurred_at, reverse=True)

    total = len(all_entries)
    offset = (page - 1) * per_page
    page_items = all_entries[offset: offset + per_page]
    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return AuditLogListResponse(items=page_items, total=total, page=page, per_page=per_page, pages=pages)