"""
Hospital Registration Service
--------------------------------
Business logic for the hospital onboarding flow.

Handles:
  - Listing available subscription plans
  - Creating the hospital record (status: pending_review)
  - Initializing the trial subscription
  - Linking the registering user to their hospital
  - Checking application status
"""

import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError

from app.config import settings
from app.models.platform.hospital import Hospital, HospitalStatus
from app.models.platform.plan import Plan
from app.models.platform.subscription import (
    Subscription,
    SubscriptionStatus,
    BillingCycle,
)
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.schemas.hospital.registration import (
    HospitalRegistrationRequest,
    HospitalRegistrationResponse,
    HospitalStatusResponse,
    PlanSummary,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    UnauthorizedException,
    ForbiddenException,
    ValidationException,
)


# ── Registration Token Decoder ────────────────────────────────────────────────

def decode_registration_token(token: str) -> str:
    """
    Decodes a hospital registration token and returns the user ID.

    Only accepts tokens of type "hospital_registration" — rejects
    access tokens or any other token type.

    Returns:
        User UUID as a string

    Raises:
        UnauthorizedException if the token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )

        if payload.get("type") != "hospital_registration":
            raise UnauthorizedException("Invalid token type for this endpoint")

        user_id = payload.get("sub")
        if not user_id:
            raise UnauthorizedException("Invalid token payload")

        return user_id

    except JWTError:
        raise UnauthorizedException(
            "Registration token is invalid or expired. Please sign in with Google again."
        )


# ── Plan Listing ──────────────────────────────────────────────────────────────

async def list_available_plans(
    platform_db: AsyncSession,
) -> list[PlanSummary]:
    """
    Returns all active, publicly visible subscription plans.

    Called during the plan selection step of onboarding.
    Ordered by display_order so the platform owner controls
    which plan appears first on the pricing page.

    Args:
        platform_db: Platform database session

    Returns:
        List of PlanSummary objects ordered for display
    """
    result = await platform_db.execute(
        select(Plan)
        .where(Plan.is_active == True)
        .where(Plan.is_public == True)
        .order_by(Plan.display_order)
    )
    plans = result.scalars().all()
    return [PlanSummary.model_validate(plan) for plan in plans]


# ── Hospital Registration ─────────────────────────────────────────────────────

async def register_hospital(
    platform_db: AsyncSession,
    tenant_db: AsyncSession,
    registration_token: str,
    data: HospitalRegistrationRequest,
) -> HospitalRegistrationResponse:
    """
    Creates the hospital record and initializes the subscription.

    This is called when the hospital admin submits the registration form.
    The result is a Hospital record with status: pending_review.

    The flow:
      1. Decode the registration token to get the user ID
      2. Load the user from the tenant database
      3. Verify they do not already have a hospital
      4. Verify the selected plan exists
      5. Generate a unique slug for the hospital
      6. Create the Hospital record (platform DB)
      7. Create the Subscription record (platform DB)
      8. Link the user to the hospital (tenant DB)
      9. Return confirmation

    Args:
        platform_db:        Platform database session
        tenant_db:          Tenant database session
        registration_token: Token from the Google OAuth callback
        data:               Hospital information from the registration form

    Returns:
        HospitalRegistrationResponse
    """

    # ── Step 1: Decode registration token ────────────────────────────────────
    user_id_str = decode_registration_token(registration_token)

    # ── Step 2: Load the user ─────────────────────────────────────────────────
    result = await tenant_db.execute(
        select(HospitalUser).where(
            HospitalUser.id == uuid.UUID(user_id_str)
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedException("User account not found. Please sign in again.")

    # ── Step 3: Check they don't already have a hospital ──────────────────────
    sentinel_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    if user.hospital_id != sentinel_id:
        raise ConflictException(
            "You have already registered a hospital. "
            "Each account can only register one hospital."
        )

    # ── Step 4: Verify the selected plan ─────────────────────────────────────
    result = await platform_db.execute(
        select(Plan).where(
            Plan.id == data.plan_id,
            Plan.is_active == True,
        )
    )
    plan = result.scalar_one_or_none()

    if not plan:
        raise NotFoundException("Subscription plan")

    # ── Step 5: Generate a unique slug ────────────────────────────────────────
    # The slug is a URL-safe version of the hospital name
    # Example: "City General Hospital" → "city-general-hospital"
    # If that slug exists, we append a number: "city-general-hospital-2"
    slug = await _generate_unique_slug(platform_db, data.name)

    # ── Step 6: Create the Hospital record ───────────────────────────────────
    hospital = Hospital(
        name=data.name,
        slug=slug,
        description=data.description,
        country=data.country,
        state=data.state,
        city=data.city,
        address=data.address,
        phone=data.phone,
        email=data.email,
        website=data.website,
        registration_number=data.registration_number,
        status=HospitalStatus.PENDING_REVIEW,
    )
    platform_db.add(hospital)
    await platform_db.flush()  # Get the hospital ID without committing

    # ── Step 7: Create the Subscription record ────────────────────────────────
    now = datetime.now(timezone.utc)

    if data.billing_cycle == "trial" and plan.has_trial:
        # Trial subscription
        subscription = Subscription(
            hospital_id=hospital.id,
            plan_id=plan.id,
            status=SubscriptionStatus.TRIAL,
            billing_cycle=BillingCycle.MONTHLY,
            trial_started_at=now,
            trial_ends_at=now + timedelta(days=plan.trial_days),
            amount_paid=0,
            currency=plan.currency,
        )
    else:
        # Paid subscription (payment handled separately via Paystack)
        billing = (
            BillingCycle.ANNUAL
            if data.billing_cycle == "annual"
            else BillingCycle.MONTHLY
        )
        subscription = Subscription(
            hospital_id=hospital.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle=billing,
            current_period_start=now,
            current_period_end=(
                now + timedelta(days=365)
                if billing == BillingCycle.ANNUAL
                else now + timedelta(days=30)
            ),
            amount_paid=float(
                plan.price_annual
                if billing == BillingCycle.ANNUAL
                else plan.price_monthly
            ),
            currency=plan.currency,
        )

    platform_db.add(subscription)

    # ── Step 8: Link the user to the hospital ─────────────────────────────────
    user.hospital_id = hospital.id
    user.role = HospitalUserRole.HOSPITAL_ADMIN
    user.is_owner = True

    # ── Step 9: Commit everything ─────────────────────────────────────────────
    await platform_db.commit()
    await tenant_db.commit()

    return HospitalRegistrationResponse(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        status=hospital.status.value,
        message=(
            "Your hospital application has been submitted successfully. "
            "Our team will review your application and notify you within 1-2 business days."
        ),
    )


async def get_registration_status(
    platform_db: AsyncSession,
    tenant_db: AsyncSession,
    registration_token: str,
) -> HospitalStatusResponse:
    """
    Returns the current status of a hospital's registration application.

    Hospital admins call this to check if their application has been approved.

    Args:
        platform_db:        Platform database session
        tenant_db:          Tenant database session
        registration_token: Token from Google OAuth

    Returns:
        HospitalStatusResponse
    """
    user_id_str = decode_registration_token(registration_token)

    result = await tenant_db.execute(
        select(HospitalUser).where(
            HospitalUser.id == uuid.UUID(user_id_str)
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedException("User not found")

    sentinel_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    if user.hospital_id == sentinel_id:
        raise NotFoundException("Hospital registration")

    # Load hospital from platform database
    result = await platform_db.execute(
        select(Hospital).where(Hospital.id == user.hospital_id)
    )
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise NotFoundException("Hospital")

    # Load subscription
    result = await platform_db.execute(
        select(Subscription).where(Subscription.hospital_id == hospital.id)
    )
    subscription = result.scalar_one_or_none()

    return HospitalStatusResponse(
        hospital_id=hospital.id,
        hospital_name=hospital.name,
        status=hospital.status.value,
        submitted_at=hospital.created_at,
        approved_at=hospital.approved_at,
        review_notes=hospital.review_notes,
        subscription_status=subscription.status.value if subscription else None,
    )


# ── Slug Generation Helper ────────────────────────────────────────────────────

async def _generate_unique_slug(db: AsyncSession, name: str) -> str:
    """
    Generates a unique URL-safe slug from a hospital name.

    Examples:
      "City General Hospital" → "city-general-hospital"
      "City General Hospital" (if taken) → "city-general-hospital-2"

    Args:
        db:   Platform database session
        name: Hospital name

    Returns:
        A unique slug string
    """
    import re

    # Convert to lowercase, replace spaces and special chars with hyphens
    base_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    # Truncate to reasonable length
    base_slug = base_slug[:80]

    slug = base_slug
    counter = 2

    while True:
        result = await db.execute(
            select(Hospital).where(Hospital.slug == slug)
        )
        if not result.scalar_one_or_none():
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1