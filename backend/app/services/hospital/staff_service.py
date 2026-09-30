"""
Staff Management Service
--------------------------
Business logic for hospital staff management.

Staff lifecycle:
  1. Admin invites staff member (creates HospitalUser with status=invited)
  2. Invitation email is sent with a secure token
  3. Staff member clicks link, sets password (status → active)
  4. Staff member can now log in via Google OAuth or email/password
  5. Admin can update role, department, or deactivate the account

Multi-tenancy enforcement:
  Every function is scoped to hospital_id from the JWT token.
  Staff from one hospital cannot be read or modified by another.

Invitation token security:
  Tokens are signed JWTs with a 72-hour expiry.
  They contain the user ID and are verified before acceptance.
  A token cannot be reused after the invitation is accepted.
"""

import uuid
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from jose import jwt, JWTError

from app.config import settings
from app.models.tenant.hospital_user import (
    HospitalUser,
    HospitalUserRole,
    HospitalUserStatus,
)
from app.models.tenant.department import Department
from app.schemas.hospital.staff import (
    StaffInviteRequest,
    StaffUpdateRequest,
    StaffMemberResponse,
    StaffListResponse,
    InvitationResponse,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    ForbiddenException,
    UnauthorizedException,
)
from app.core.security import hash_password
from app.utils.email import send_staff_invitation_email

INVITATION_TOKEN_EXPIRE_HOURS = 72


# ── Invitation Token Helpers ──────────────────────────────────────────────────

def _create_invitation_token(user_id: str, email: str) -> str:
    """
    Creates a signed JWT for a staff invitation.

    The token encodes:
      - The user ID (so we know which record to activate)
      - The email (so we can verify it matches on acceptance)
      - An expiry (72 hours — tokens cannot be reused after this)
      - A type claim (prevents other token types from being used here)

    Args:
        user_id: UUID string of the HospitalUser record
        email:   Email address of the invited staff member

    Returns:
        Signed JWT string
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=INVITATION_TOKEN_EXPIRE_HOURS)

    payload = {
        "sub": user_id,
        "email": email,
        "type": "staff_invitation",
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def _decode_invitation_token(token: str) -> dict:
    """
    Decodes and validates a staff invitation token.

    Returns:
        Payload dict with sub (user_id) and email

    Raises:
        UnauthorizedException if the token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )

        if payload.get("type") != "staff_invitation":
            raise UnauthorizedException("Invalid token type")

        return payload

    except JWTError:
        raise UnauthorizedException(
            "This invitation link is invalid or has expired. "
            "Please ask your administrator to send a new invitation."
        )


# ── Role Validation ───────────────────────────────────────────────────────────

def _validate_role(role: str) -> HospitalUserRole:
    """
    Validates and returns the HospitalUserRole enum value.

    Raises ValidationException if the role string is not valid.
    """
    valid_roles = {r.value for r in HospitalUserRole}

    if role not in valid_roles:
        raise ValidationException(
            f"'{role}' is not a valid role. "
            f"Valid roles are: {', '.join(sorted(valid_roles))}"
        )

    return HospitalUserRole(role)


# ── Core Staff Operations ─────────────────────────────────────────────────────

async def invite_staff_member(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    inviting_admin: HospitalUser,
    data: StaffInviteRequest,
) -> InvitationResponse:
    """
    Invites a new staff member to the hospital workspace.

    Flow:
      1. Validate the role string
      2. Validate the department (if provided) belongs to this hospital
      3. Check the email is not already registered in this hospital
      4. Create a HospitalUser record with status=invited
      5. Generate an invitation token
      6. Send the invitation email
      7. Return confirmation

    Args:
        db:             Tenant database session
        hospital_id:    From the JWT token
        inviting_admin: The admin performing the invitation (for the email)
        data:           Invitation details from the request body

    Returns:
        InvitationResponse
    """

    # Step 1: Validate role
    role_enum = _validate_role(data.role)

    # Step 2: Validate department if provided
    if data.department_id:
        dept_result = await db.execute(
            select(Department).where(
                Department.id == data.department_id,
                Department.hospital_id == hospital_id,
                Department.is_active == True,
            )
        )
        if not dept_result.scalar_one_or_none():
            raise NotFoundException("Department")

    # Step 3: Check email uniqueness within this hospital
    existing_result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.email == data.email.lower(),
            HospitalUser.hospital_id == hospital_id,
        )
    )
    if existing_result.scalar_one_or_none():
        raise ConflictException(
            f"A staff member with email '{data.email}' already exists "
            f"in your hospital."
        )

    # Step 4: Create the staff record with status=invited
    new_staff = HospitalUser(
        hospital_id=hospital_id,
        full_name=data.full_name,
        email=data.email.lower(),
        role=role_enum,
        department_id=data.department_id,
        specialization=data.specialization,
        license_number=data.license_number,
        status=HospitalUserStatus.INVITED,
        is_owner=False,
    )
    db.add(new_staff)
    await db.flush()   # Get the ID before committing

    # Step 5: Generate invitation token
    invitation_token = _create_invitation_token(
        user_id=str(new_staff.id),
        email=new_staff.email,
    )

    # Step 6: Send invitation email
    # We commit the record first so it exists even if email fails.
    # The admin can resend the email from the staff list.
    await db.commit()
    await db.refresh(new_staff)

    email_sent = await send_staff_invitation_email(
        to_email=new_staff.email,
        to_name=new_staff.full_name,
        hospital_name="your hospital",  # Updated when we have hospital lookup
        inviter_name=inviting_admin.full_name,
        invitation_token=invitation_token,
        role=new_staff.role.value,
    )

    # Step 7: Return confirmation
    return InvitationResponse(
        message=(
            "Invitation sent successfully."
            if email_sent
            else (
                "Staff member added but the invitation email could not be sent. "
                "Please use the resend option from the staff list."
            )
        ),
        staff_id=new_staff.id,
        email=new_staff.email,
        role=new_staff.role.value,
        invitation_sent=email_sent,
    )


async def accept_invitation(
    db: AsyncSession,
    data: "InvitationAcceptRequest",
) -> StaffMemberResponse:
    """
    Processes a staff member's acceptance of their invitation.

    Called when the invited person clicks the email link,
    lands on the frontend, and submits the password form.

    Flow:
      1. Decode and validate the invitation token
      2. Load the user record
      3. Verify the user is still in invited status
      4. Validate the passwords match
      5. Set the password hash
      6. Activate the account (status → active)
      7. Return the user profile

    Args:
        db:   Tenant database session
        data: Token and password from the accept form

    Returns:
        StaffMemberResponse (the activated user)
    """
    from app.schemas.hospital.staff import InvitationAcceptRequest

    # Step 1: Decode the invitation token
    payload = _decode_invitation_token(data.invitation_token)
    user_id_str = payload.get("sub")
    token_email = payload.get("email")

    # Step 2: Load the user
    result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == uuid.UUID(user_id_str)
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedException("Invitation is invalid. User not found.")

    # Verify the email in the token matches the user's email
    if user.email != token_email:
        raise UnauthorizedException("Invitation token does not match user email.")

    # Step 3: Verify status is still invited
    if user.status == HospitalUserStatus.ACTIVE:
        raise ConflictException(
            "This invitation has already been accepted. "
            "Please log in with your existing account."
        )

    if user.status == HospitalUserStatus.INACTIVE:
        raise ForbiddenException(
            "This account has been deactivated. "
            "Please contact your hospital administrator."
        )

    # Step 4: Validate passwords match
    if data.password != data.confirm_password:
        raise ValidationException("Passwords do not match.")

    # Step 5 & 6: Set password and activate
    user.hashed_password = hash_password(data.password)
    user.status = HospitalUserStatus.ACTIVE

    await db.commit()
    await db.refresh(user)

    return StaffMemberResponse.model_validate(user)


async def list_staff(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    role_filter: str | None = None,
    department_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    search: str | None = None,
) -> StaffListResponse:
    """
    Returns a paginated list of staff members for a hospital.

    Supports filtering by role, department, status, and name search.

    Args:
        db:            Tenant database session
        hospital_id:   From the JWT token — scopes to one hospital
        page:          Page number
        per_page:      Results per page
        role_filter:   Filter by role string
        department_id: Filter by department
        status_filter: Filter by status (active, invited, inactive)
        search:        Search by name or email

    Returns:
        StaffListResponse
    """
    query = (
        select(HospitalUser)
        .where(HospitalUser.hospital_id == hospital_id)
        .order_by(HospitalUser.full_name)
    )

    # Apply filters
    if role_filter:
        try:
            role_enum = HospitalUserRole(role_filter)
            query = query.where(HospitalUser.role == role_enum)
        except ValueError:
            pass  # Invalid role filter — ignore

    if department_id:
        query = query.where(HospitalUser.department_id == department_id)

    if status_filter:
        try:
            status_enum = HospitalUserStatus(status_filter)
            query = query.where(HospitalUser.status == status_enum)
        except ValueError:
            pass

    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                HospitalUser.full_name.ilike(search_term),
                HospitalUser.email.ilike(search_term),
            )
        )

    # Count total matching records
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    staff_members = result.scalars().all()

    return StaffListResponse(
        items=[StaffMemberResponse.model_validate(s) for s in staff_members],
        total=total,
        page=page,
        per_page=per_page,
    )


async def get_staff_member(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    staff_id: uuid.UUID,
) -> StaffMemberResponse:
    """
    Fetches a single staff member by ID.

    The hospital_id filter ensures you can only fetch your own staff.
    """
    result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == staff_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise NotFoundException("Staff member")

    return StaffMemberResponse.model_validate(user)


async def update_staff_member(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    staff_id: uuid.UUID,
    data: StaffUpdateRequest,
    requesting_user: HospitalUser,
) -> StaffMemberResponse:
    """
    Updates a staff member's profile.

    Rules:
      - A hospital_admin can update any staff member
      - A staff member can update their own profile (limited fields)
      - No one can change their own role (prevents privilege escalation)
      - No one can change the hospital owner's role

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        staff_id:         ID of the staff member to update
        data:             Fields to update
        requesting_user:  The user making the request (for permission check)

    Returns:
        Updated StaffMemberResponse
    """
    result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == staff_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    staff = result.scalar_one_or_none()

    if not staff:
        raise NotFoundException("Staff member")

    is_self_update = requesting_user.id == staff.id
    is_admin = requesting_user.role == HospitalUserRole.HOSPITAL_ADMIN

    # Non-admins can only update their own profile
    if not is_admin and not is_self_update:
        raise ForbiddenException(
            "You can only update your own profile."
        )

    # Role changes require admin privileges
    if data.role is not None:
        if not is_admin:
            raise ForbiddenException(
                "Only hospital administrators can change staff roles."
            )
        # Protect the owner's role from being changed
        if staff.is_owner:
            raise ForbiddenException(
                "The hospital owner's role cannot be changed."
            )
        data_role = _validate_role(data.role)
        staff.role = data_role

    # Department changes require admin or self-update
    if data.department_id is not None:
        if not is_admin and not is_self_update:
            raise ForbiddenException(
                "Only hospital administrators can change department assignments."
            )
        # Validate department belongs to this hospital
        dept_result = await db.execute(
            select(Department).where(
                Department.id == data.department_id,
                Department.hospital_id == hospital_id,
                Department.is_active == True,
            )
        )
        if not dept_result.scalar_one_or_none():
            raise NotFoundException("Department")
        staff.department_id = data.department_id

    # Apply remaining fields
    if data.full_name is not None:
        staff.full_name = data.full_name
    if data.specialization is not None:
        staff.specialization = data.specialization
    if data.license_number is not None:
        staff.license_number = data.license_number

    await db.commit()
    await db.refresh(staff)

    return StaffMemberResponse.model_validate(staff)


async def deactivate_staff_member(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    staff_id: uuid.UUID,
    requesting_user: HospitalUser,
) -> dict:
    """
    Deactivates a staff member's account.

    Rules:
      - Only hospital_admin can deactivate staff
      - Cannot deactivate yourself
      - Cannot deactivate the hospital owner

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        staff_id:         ID of the staff member to deactivate
        requesting_user:  The admin performing the action

    Returns:
        Success message dict
    """
    result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == staff_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    staff = result.scalar_one_or_none()

    if not staff:
        raise NotFoundException("Staff member")

    if staff.id == requesting_user.id:
        raise ForbiddenException(
            "You cannot deactivate your own account."
        )

    if staff.is_owner:
        raise ForbiddenException(
            "The hospital owner account cannot be deactivated."
        )

    staff.status = HospitalUserStatus.INACTIVE
    await db.commit()

    return {
        "message": f"Staff member '{staff.full_name}' has been deactivated.",
        "staff_id": str(staff.id),
    }


async def reactivate_staff_member(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    staff_id: uuid.UUID,
) -> dict:
    """
    Reactivates a previously deactivated staff member.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token
        staff_id:    ID of the staff member to reactivate

    Returns:
        Success message dict
    """
    result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == staff_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    staff = result.scalar_one_or_none()

    if not staff:
        raise NotFoundException("Staff member")

    if staff.status != HospitalUserStatus.INACTIVE:
        raise ValidationException(
            "Only inactive staff members can be reactivated."
        )

    staff.status = HospitalUserStatus.ACTIVE
    await db.commit()

    return {
        "message": f"Staff member '{staff.full_name}' has been reactivated.",
        "staff_id": str(staff.id),
    }