"""
Platform Audit Log Router
--------------------------------
Read-only cross-database audit trail for platform admins. Aggregates
actor/timestamp data already captured across existing tables — see
audit_log_service.py for the full source list and the known gaps
where no actor was historically captured.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_platform_db, get_tenant_db
from app.core.dependencies import get_current_platform_admin
from app.schemas.platform.audit_log import AuditLogListResponse
from app.services.platform.audit_log_service import list_audit_log

router = APIRouter()


@router.get(
    "/",
    response_model=AuditLogListResponse,
    summary="List Audit Log",
    description=(
        "Returns a unified, paginated audit feed aggregated from actor/timestamp "
        "columns across both databases. Optionally filter by hospital or source table."
    ),
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    hospital_id: UUID | None = Query(default=None),
    source_table: str | None = Query(
        default=None,
        description="hospitals | subscription_events | subscriptions | support_tickets | announcements | invoices | prescriptions | lab_orders | generated_documents",
    ),
    platform_db: AsyncSession = Depends(get_platform_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    admin=Depends(get_current_platform_admin),
):
    return await list_audit_log(platform_db, tenant_db, page, per_page, hospital_id, source_table)