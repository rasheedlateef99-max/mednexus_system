"""
Platform Analytics Schemas
-------------------------------
Response shapes for the platform owner's analytics dashboard.
Entirely read-only — no request bodies beyond query parameters.
"""

from pydantic import BaseModel
from datetime import date


class PlatformSummaryResponse(BaseModel):
    """Top-level KPI snapshot for the platform owner dashboard."""
    total_hospitals: int
    active_hospitals: int
    trial_hospitals: int
    suspended_hospitals: int
    pending_review_hospitals: int
    deactivated_hospitals: int
    monthly_recurring_revenue: float
    total_revenue_this_month: float
    new_hospitals_this_month: int


class MonthlySignupPoint(BaseModel):
    month: str  # "2026-01"
    signup_count: int


class HospitalGrowthResponse(BaseModel):
    months: list[MonthlySignupPoint]


class PlanRevenueBreakdown(BaseModel):
    plan_id: str
    plan_name: str
    active_subscribers: int
    monthly_revenue: float


class RevenueBreakdownResponse(BaseModel):
    by_plan: list[PlanRevenueBreakdown]
    total_monthly_revenue: float


class StatusDistributionItem(BaseModel):
    status: str
    count: int


class SubscriptionStatusDistributionResponse(BaseModel):
    distribution: list[StatusDistributionItem]
    total: int


class RecentActivityItem(BaseModel):
    hospital_id: str
    hospital_name: str
    event_type: str
    amount: float | None
    currency: str | None
    created_at: str


class RecentActivityResponse(BaseModel):
    items: list[RecentActivityItem]