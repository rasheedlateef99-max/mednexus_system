"""
Platform Subscription Router
---------------------------------
Endpoints for subscription payment, status, and the Paystack webhook.

The webhook endpoint is UNAUTHENTICATED by design — Paystack calls it
directly, it has no JWT. Security comes entirely from signature
verification (verify_webhook_signature), not from auth middleware.
This is standard practice for all payment provider webhooks.

Rate limiting (M21):
  /webhooks/paystack — 60/minute per IP (high enough for Paystack's
  retry logic, blocks flood attacks)
"""

from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_platform_db
from app.core.dependencies import get_current_platform_admin
from app.schemas.platform.subscription import (
    PaymentInitializeRequest,
    PaymentInitializeResponse,
    SubscriptionStatusResponse,
    SubscriptionCancelRequest,
    SubscriptionEventListResponse,
)
from app.services.platform.subscription_service import (
    initialize_subscription_payment,
    process_payment_webhook,
    get_subscription_status,
    cancel_subscription,
    list_subscription_events,
)
from app.services.platform.paystack_provider import get_payment_provider
from app.models.platform.hospital import Hospital
from sqlalchemy import select
from app.core.exceptions import NotFoundException
from app.core.rate_limiter import limiter
import json

router = APIRouter()


@router.post(
    "/{hospital_id}/payments/initialize",
    response_model=PaymentInitializeResponse,
    summary="Initialize Subscription Payment",
    description="Starts a Paystack payment session for a hospital's plan subscription or renewal.",
)
async def initialize_payment(
    hospital_id: UUID,
    data: PaymentInitializeRequest,
    db: AsyncSession = Depends(get_platform_db),
):
    hospital_result = await db.execute(select(Hospital).where(Hospital.id == hospital_id))
    hospital = hospital_result.scalar_one_or_none()
    if not hospital or not hospital.email:
        raise NotFoundException("Hospital with a valid email on file")

    return await initialize_subscription_payment(db, hospital_id, hospital.email, data)


@router.post(
    "/webhooks/paystack",
    summary="Paystack Webhook",
    description="Receives and verifies Paystack payment events. No authentication — secured by signature verification.",
)
@limiter.limit("60/minute")
async def paystack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_platform_db),
):
    """
    Rate limited: 60 requests/minute per IP.
    Signature verification provides the primary security layer.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    provider = get_payment_provider()
    if not provider.verify_webhook_signature(raw_body, signature):
        return {"success": False}

    payload = json.loads(raw_body)
    event_type = payload.get("event", "")
    data = payload.get("data", {})

    result = await process_payment_webhook(db, event_type, data)
    return {"success": True, "detail": result}


@router.get(
    "/{hospital_id}/status",
    response_model=SubscriptionStatusResponse,
    summary="Get Subscription Status",
    description="Returns the current subscription state for a hospital. Platform admin only.",
)
async def status(
    hospital_id: UUID,
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await get_subscription_status(db, hospital_id)


@router.post(
    "/{hospital_id}/cancel",
    summary="Cancel Subscription",
    description="Cancels a hospital's subscription. Platform admin only.",
)
async def cancel(
    hospital_id: UUID,
    data: SubscriptionCancelRequest,
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await cancel_subscription(db, hospital_id, data)


@router.get(
    "/{hospital_id}/events",
    response_model=SubscriptionEventListResponse,
    summary="Subscription Event History",
    description="Returns the audit log of subscription lifecycle events for a hospital. Platform admin only.",
)
async def events(
    hospital_id: UUID,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_platform_db),
    admin=Depends(get_current_platform_admin),
):
    return await list_subscription_events(db, hospital_id, page, per_page)