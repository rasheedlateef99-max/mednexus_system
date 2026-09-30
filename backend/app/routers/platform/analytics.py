"""
Platform Analytics Router
-------------------------------
Read-only reporting endpoints for the LATLOMP platform owner dashboard.
All routes require platform admin authentication.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_platform_db
from app.core.dependencies import get_current_platform_admin
from app.schemas.platform.analytics import (
    PlatformSummaryResponse,
    HospitalGrowthResponse,
    RevenueBreakdownResponse,
    SubscriptionStatusDistributionResponse,
    RecentActivityResponse,
)
from app.services.platform.analytics_service import (
    get_platform_summary,
    get_hospital_growth,
    get_revenue_breakdown,
    get_subscription_status_distribution,
    get_recent_activity,
)

router = APIRouter()


@router.get(
    "/summary",
    response_model=PlatformSummaryResponse,
    summary="Platform Summary",
    description="Top-level KPIs: hospital counts by status, MRR, monthly revenue, new signups.",
)
async def summary(
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await get_platform_summary(db)


@router.get(
    "/growth",
    response_model=HospitalGrowthResponse,
    summary="Hospital Growth",
    description="Hospital signups grouped by month.",
)
async def growth(
    months_back: int = Query(default=6, ge=1, le=24),
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await get_hospital_growth(db, months_back)


@router.get(
    "/revenue",
    response_model=RevenueBreakdownResponse,
    summary="Revenue Breakdown by Plan",
    description="Active subscriber count and monthly-equivalent revenue per plan.",
)
async def revenue(
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await get_revenue_breakdown(db)


@router.get(
    "/subscription-status",
    response_model=SubscriptionStatusDistributionResponse,
    summary="Subscription Status Distribution",
    description="Count of subscriptions grouped by status.",
)
async def subscription_status(
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await get_subscription_status_distribution(db)


@router.get(
    "/activity",
    response_model=RecentActivityResponse,
    summary="Recent Activity Feed",
    description="Latest subscription events across all hospitals, most recent first.",
)
async def activity(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await get_recent_activity(db, limit)