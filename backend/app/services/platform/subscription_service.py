"""
Subscription Management Service
------------------------------------
Business logic for hospital subscription lifecycle: payment initialization,
webhook-driven confirmation, plan changes, cancellation, and the
enforcement check tenant routes can call to verify a hospital's
subscription is active.

Webhook Processing Flow:
  1. Raw body + signature header arrive at the webhook endpoint
  2. verify_webhook_signature() confirms authenticity (see paystack_provider.py)
  3. If valid: parse event type, find matching SubscriptionEvent by reference
  4. If event is "charge.success": activate/renew the subscription,
     extend current_period_end, log SUBSCRIPTION_ACTIVATED or _RENEWED
  5. Always log the raw event for audit, even if we don't act on it

Idempotency:
  Webhooks can be delivered more than once for the same event (Paystack
  retries on timeout). Before activating a subscription, we check
  whether a PAYMENT_CONFIRMED event already exists for this exact
  reference — if so, we skip re-activation and just log a duplicate note.
"""

import uuid
import secrets
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.platform.hospital import Hospital, HospitalStatus
from app.models.platform.plan import Plan
from app.models.platform.subscription import Subscription, SubscriptionStatus, BillingCycle
from app.models.platform.subscription_event import SubscriptionEvent, SubscriptionEventType
from app.schemas.platform.subscription import (
    PaymentInitializeRequest,
    PaymentInitializeResponse,
    SubscriptionStatusResponse,
    SubscriptionChangeRequest,
    SubscriptionCancelRequest,
    SubscriptionEventResponse,
    SubscriptionEventListResponse,
)
from app.services.platform.paystack_provider import get_payment_provider
from app.core.exceptions import NotFoundException, ValidationException, ForbiddenException


def _generate_payment_reference(hospital_id: uuid.UUID) -> str:
    """Unique, traceable reference: MNX-<hospital prefix>-<random>."""
    return f"MNX-{str(hospital_id)[:8]}-{secrets.token_hex(6)}"


async def initialize_subscription_payment(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    hospital_email: str,
    data: PaymentInitializeRequest,
) -> PaymentInitializeResponse:
    """Starts a Paystack payment session for a plan subscription/renewal."""
    plan_result = await db.execute(
        select(Plan).where(Plan.id == data.plan_id, Plan.is_active == True)
    )
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise NotFoundException("Plan")

    amount = float(plan.price_annual if data.billing_cycle == "annual" else plan.price_monthly)
    reference = _generate_payment_reference(hospital_id)

    provider = get_payment_provider()
    result = await provider.initialize_payment(
        email=hospital_email,
        amount=amount,
        currency=plan.currency,
        reference=reference,
        callback_url=data.callback_url,
        metadata={"hospital_id": str(hospital_id), "plan_id": str(plan.id)},
    )

    event = SubscriptionEvent(
        hospital_id=hospital_id,
        plan_id=plan.id,
        event_type=SubscriptionEventType.PAYMENT_INITIALIZED,
        amount=amount,
        currency=plan.currency,
        payment_reference=reference,
        notes=f"Payment initialized for {data.billing_cycle} billing cycle",
    )
    db.add(event)
    await db.commit()

    return PaymentInitializeResponse(**result)


async def process_payment_webhook(
    db: AsyncSession,
    event_type: str,
    paystack_data: dict,
) -> dict:
    """
    Processes a verified Paystack webhook payload.
    Caller MUST have already verified the signature before calling this.
    """
    reference = paystack_data.get("reference")
    if not reference:
        return {"processed": False, "message": "No reference in payload"}

    # Idempotency check — has this exact reference already been confirmed?
    existing = await db.execute(
        select(SubscriptionEvent).where(
            SubscriptionEvent.payment_reference == reference,
            SubscriptionEvent.event_type == SubscriptionEventType.PAYMENT_CONFIRMED,
        )
    )
    if existing.scalar_one_or_none():
        return {"processed": True, "message": "Duplicate webhook — already processed"}

    metadata = paystack_data.get("metadata", {}) or {}
    hospital_id_str = metadata.get("hospital_id")
    plan_id_str = metadata.get("plan_id")

    if not hospital_id_str or not plan_id_str:
        return {"processed": False, "message": "Missing hospital_id/plan_id in metadata"}

    hospital_id = uuid.UUID(hospital_id_str)
    plan_id = uuid.UUID(plan_id_str)

    if event_type != "charge.success":
        log = SubscriptionEvent(
            hospital_id=hospital_id,
            plan_id=plan_id,
            event_type=SubscriptionEventType.PAYMENT_FAILED,
            payment_reference=reference,
            notes=f"Webhook event: {event_type}",
        )
        db.add(log)
        await db.commit()
        return {"processed": True, "message": f"Logged non-success event: {event_type}"}

    plan_result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = plan_result.scalar_one_or_none()
    if not plan:
        return {"processed": False, "message": "Plan not found"}

    sub_result = await db.execute(
        select(Subscription).where(Subscription.hospital_id == hospital_id)
    )
    subscription = sub_result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    amount_paid = float(paystack_data.get("amount", 0)) / 100  # subunits → main unit

    is_renewal = subscription is not None and subscription.status in (
        SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.EXPIRED,
    )

    if not subscription:
        subscription = Subscription(hospital_id=hospital_id, plan_id=plan_id)
        db.add(subscription)

    period_days = 365 if subscription.billing_cycle == BillingCycle.ANNUAL else 30
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.plan_id = plan_id
    subscription.current_period_start = now
    subscription.current_period_end = now + timedelta(days=period_days)
    subscription.amount_paid = amount_paid
    subscription.currency = plan.currency
    subscription.payment_provider = "paystack"
    subscription.external_subscription_id = reference

    await db.commit()
    await db.refresh(subscription)

    confirm_event = SubscriptionEvent(
        hospital_id=hospital_id,
        subscription_id=subscription.id,
        plan_id=plan_id,
        event_type=SubscriptionEventType.PAYMENT_CONFIRMED,
        amount=amount_paid,
        currency=plan.currency,
        payment_reference=reference,
    )
    db.add(confirm_event)

    lifecycle_event = SubscriptionEvent(
        hospital_id=hospital_id,
        subscription_id=subscription.id,
        plan_id=plan_id,
        event_type=(
            SubscriptionEventType.SUBSCRIPTION_RENEWED if is_renewal
            else SubscriptionEventType.SUBSCRIPTION_ACTIVATED
        ),
        payment_reference=reference,
    )
    db.add(lifecycle_event)
    await db.commit()

    return {"processed": True, "message": "Subscription activated/renewed successfully"}


async def get_subscription_status(
    db: AsyncSession, hospital_id: uuid.UUID,
) -> SubscriptionStatusResponse:
    """Used by tenant-side middleware to check whether a hospital can operate."""
    sub_result = await db.execute(
        select(Subscription).where(Subscription.hospital_id == hospital_id)
    )
    subscription = sub_result.scalar_one_or_none()
    if not subscription:
        raise NotFoundException("Subscription")

    plan_result = await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
    plan = plan_result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    days_remaining = None
    is_active = False

    if subscription.status == SubscriptionStatus.TRIAL and subscription.trial_ends_at:
        days_remaining = max(0, (subscription.trial_ends_at - now).days)
        is_active = now < subscription.trial_ends_at
    elif subscription.status == SubscriptionStatus.ACTIVE and subscription.current_period_end:
        days_remaining = max(0, (subscription.current_period_end - now).days)
        is_active = now < subscription.current_period_end
    elif subscription.status == SubscriptionStatus.PAST_DUE:
        is_active = False

    return SubscriptionStatusResponse(
        hospital_id=hospital_id,
        plan_name=plan.name if plan else "Unknown",
        status=subscription.status.value,
        billing_cycle=subscription.billing_cycle.value,
        trial_ends_at=subscription.trial_ends_at,
        current_period_end=subscription.current_period_end,
        days_remaining=days_remaining,
        is_active=is_active,
    )


async def check_subscription_active(db: AsyncSession, hospital_id: uuid.UUID) -> bool:
    """
    Lightweight boolean check for tenant-route enforcement middleware.
    Does NOT raise — returns False on any issue so callers decide how to respond.
    """
    try:
        status = await get_subscription_status(db, hospital_id)
        return status.is_active
    except NotFoundException:
        return False


async def cancel_subscription(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    data: SubscriptionCancelRequest,
) -> dict:
    sub_result = await db.execute(
        select(Subscription).where(Subscription.hospital_id == hospital_id)
    )
    subscription = sub_result.scalar_one_or_none()
    if not subscription:
        raise NotFoundException("Subscription")

    if subscription.status == SubscriptionStatus.CANCELLED:
        raise ValidationException("Subscription is already cancelled.")

    subscription.status = SubscriptionStatus.CANCELLED
    subscription.cancelled_at = datetime.now(timezone.utc)
    subscription.cancellation_reason = data.reason
    subscription.auto_renew = False

    await db.commit()

    event = SubscriptionEvent(
        hospital_id=hospital_id,
        subscription_id=subscription.id,
        event_type=SubscriptionEventType.SUBSCRIPTION_CANCELLED,
        notes=data.reason,
    )
    db.add(event)
    await db.commit()

    return {"message": "Subscription cancelled.", "hospital_id": str(hospital_id)}


async def list_subscription_events(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
) -> SubscriptionEventListResponse:
    query = (
        select(SubscriptionEvent)
        .where(SubscriptionEvent.hospital_id == hospital_id)
        .order_by(SubscriptionEvent.created_at.desc())
    )
    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    events = result.scalars().all()

    return SubscriptionEventListResponse(
        items=[SubscriptionEventResponse.model_validate(e) for e in events],
        total=total, page=page, per_page=per_page,
    )