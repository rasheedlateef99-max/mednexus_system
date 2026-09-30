"""
Subscription Management Schemas
------------------------------------
Request/response shapes for subscription lifecycle and payment initialization.
"""

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime, date


class PaymentInitializeRequest(BaseModel):
    """Body for starting a payment for a plan (new sub, renewal, or upgrade)."""
    plan_id: UUID = Field(..., description="Plan being paid for")
    billing_cycle: str = Field(default="monthly", description="monthly | annual")
    callback_url: str | None = Field(
        None, description="Where Paystack redirects after payment (frontend URL)",
    )


class PaymentInitializeResponse(BaseModel):
    authorization_url: str = Field(..., description="Redirect the user here to pay")
    access_code: str
    reference: str


class WebhookVerificationResult(BaseModel):
    """Internal result of processing a webhook — not exposed as an API response."""
    processed: bool
    event_type: str
    reference: str | None = None
    message: str


class SubscriptionStatusResponse(BaseModel):
    """Current subscription state for a hospital, used for enforcement checks."""
    hospital_id: UUID
    plan_name: str
    status: str
    billing_cycle: str
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    days_remaining: int | None
    is_active: bool


class SubscriptionChangeRequest(BaseModel):
    """Body for changing a hospital's plan (upgrade/downgrade)."""
    new_plan_id: UUID


class SubscriptionCancelRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class SubscriptionEventResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    event_type: str
    plan_id: UUID | None
    amount: float | None
    currency: str | None
    payment_reference: str | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SubscriptionEventListResponse(BaseModel):
    items: list[SubscriptionEventResponse]
    total: int
    page: int
    per_page: int