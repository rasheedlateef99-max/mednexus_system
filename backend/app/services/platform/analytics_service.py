"""
Platform Analytics Service
--------------------------------
Aggregation queries over existing platform tables. No new data is
written here — this is pure read/reporting logic.

MRR Calculation:
  Monthly Recurring Revenue = sum of (plan price, normalized to monthly)
  across all ACTIVE subscriptions only. Annual plans are divided by 12
  to express them as a monthly-equivalent figure, which is the standard
  SaaS convention for MRR.

Performance note:
  These queries run GROUP BY and aggregate functions directly in
  PostgreSQL rather than pulling rows into Python and summing them —
  this matters once hospital count grows into the thousands.
"""

from datetime import datetime, timezone, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, extract, case

from app.models.platform.hospital import Hospital, HospitalStatus
from app.models.platform.subscription import Subscription, SubscriptionStatus, BillingCycle
from app.models.platform.plan import Plan
from app.models.platform.subscription_event import SubscriptionEvent, SubscriptionEventType
from app.schemas.platform.analytics import (
    PlatformSummaryResponse,
    HospitalGrowthResponse,
    MonthlySignupPoint,
    RevenueBreakdownResponse,
    PlanRevenueBreakdown,
    SubscriptionStatusDistributionResponse,
    StatusDistributionItem,
    RecentActivityResponse,
    RecentActivityItem,
)


async def get_platform_summary(db: AsyncSession) -> PlatformSummaryResponse:
    """Top-level dashboard KPIs."""

    async def count_status(status: HospitalStatus) -> int:
        result = await db.execute(
            select(func.count(Hospital.id)).where(Hospital.status == status)
        )
        return result.scalar() or 0

    total_result = await db.execute(select(func.count(Hospital.id)))
    total_hospitals = total_result.scalar() or 0

    # MRR: active subscriptions, normalized to monthly
    mrr_result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (Subscription.billing_cycle == BillingCycle.ANNUAL,
                         Plan.price_annual / 12),
                        else_=Plan.price_monthly,
                    )
                ),
                0,
            )
        )
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(Subscription.status == SubscriptionStatus.ACTIVE)
    )
    mrr = float(mrr_result.scalar() or 0)

    today = date.today()
    month_start = today.replace(day=1)

    revenue_this_month_result = await db.execute(
        select(func.coalesce(func.sum(SubscriptionEvent.amount), 0)).where(
            SubscriptionEvent.event_type == SubscriptionEventType.PAYMENT_CONFIRMED,
            func.date(SubscriptionEvent.created_at) >= month_start,
        )
    )
    revenue_this_month = float(revenue_this_month_result.scalar() or 0)

    new_hospitals_result = await db.execute(
        select(func.count(Hospital.id)).where(
            func.date(Hospital.created_at) >= month_start,
        )
    )
    new_hospitals_this_month = new_hospitals_result.scalar() or 0

    return PlatformSummaryResponse(
        total_hospitals=total_hospitals,
        active_hospitals=await count_status(HospitalStatus.ACTIVE),
        trial_hospitals=0,  # trial is a Subscription status, not Hospital status — see note below
        suspended_hospitals=await count_status(HospitalStatus.SUSPENDED),
        pending_review_hospitals=await count_status(HospitalStatus.PENDING_REVIEW),
        deactivated_hospitals=await count_status(HospitalStatus.DEACTIVATED),
        monthly_recurring_revenue=mrr,
        total_revenue_this_month=revenue_this_month,
        new_hospitals_this_month=new_hospitals_this_month,
    )


async def get_hospital_growth(db: AsyncSession, months_back: int = 6) -> HospitalGrowthResponse:
    """Hospital signups grouped by calendar month, most recent N months."""
    result = await db.execute(
        select(
            func.to_char(Hospital.created_at, 'YYYY-MM').label('month'),
            func.count(Hospital.id).label('signup_count'),
        )
        .group_by('month')
        .order_by('month')
    )
    rows = result.all()

    points = [
        MonthlySignupPoint(month=row.month, signup_count=row.signup_count)
        for row in rows
    ]
    # Keep only the most recent N months
    points = points[-months_back:] if len(points) > months_back else points

    return HospitalGrowthResponse(months=points)


async def get_revenue_breakdown(db: AsyncSession) -> RevenueBreakdownResponse:
    """Active subscriber count and monthly-equivalent revenue, grouped by plan."""
    result = await db.execute(
        select(
            Plan.id,
            Plan.name,
            func.count(Subscription.id).label('subscriber_count'),
            func.coalesce(
                func.sum(
                    case(
                        (Subscription.billing_cycle == BillingCycle.ANNUAL,
                         Plan.price_annual / 12),
                        else_=Plan.price_monthly,
                    )
                ),
                0,
            ).label('monthly_revenue'),
        )
        .select_from(Plan)
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(Subscription.status == SubscriptionStatus.ACTIVE)
        .group_by(Plan.id, Plan.name)
        .order_by(Plan.display_order)
    )
    rows = result.all()

    by_plan = [
        PlanRevenueBreakdown(
            plan_id=str(row.id),
            plan_name=row.name,
            active_subscribers=row.subscriber_count,
            monthly_revenue=float(row.monthly_revenue),
        )
        for row in rows
    ]
    total = sum(p.monthly_revenue for p in by_plan)

    return RevenueBreakdownResponse(by_plan=by_plan, total_monthly_revenue=total)


async def get_subscription_status_distribution(
    db: AsyncSession,
) -> SubscriptionStatusDistributionResponse:
    """Count of subscriptions per status — for a pie/bar chart."""
    result = await db.execute(
        select(Subscription.status, func.count(Subscription.id))
        .group_by(Subscription.status)
    )
    rows = result.all()

    distribution = [
        StatusDistributionItem(status=row[0].value, count=row[1])
        for row in rows
    ]
    total = sum(item.count for item in distribution)

    return SubscriptionStatusDistributionResponse(distribution=distribution, total=total)


async def get_recent_activity(db: AsyncSession, limit: int = 20) -> RecentActivityResponse:
    """Latest subscription events across ALL hospitals — platform-wide activity feed."""
    result = await db.execute(
        select(SubscriptionEvent, Hospital.name)
        .join(Hospital, SubscriptionEvent.hospital_id == Hospital.id)
        .order_by(SubscriptionEvent.created_at.desc())
        .limit(limit)
    )
    rows = result.all()

    items = [
        RecentActivityItem(
            hospital_id=str(event.hospital_id),
            hospital_name=hospital_name,
            event_type=event.event_type.value,
            amount=float(event.amount) if event.amount is not None else None,
            currency=event.currency,
            created_at=event.created_at.isoformat(),
        )
        for event, hospital_name in rows
    ]

    return RecentActivityResponse(items=items)