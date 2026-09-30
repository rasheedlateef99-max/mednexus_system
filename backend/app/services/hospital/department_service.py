"""
Department Service
--------------------
Business logic for hospital department management.

Multi-tenancy rule enforced here:
  Every function receives hospital_id extracted from the JWT token.
  Every database query filters by hospital_id.
  It is structurally impossible to read or modify another
  hospital's departments through this service.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.tenant.department import Department
from app.models.tenant.hospital_user import HospitalUser
from app.schemas.hospital.department import (
    DepartmentCreateRequest,
    DepartmentUpdateRequest,
    DepartmentResponse,
    DepartmentListResponse,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
)


async def create_department(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    data: DepartmentCreateRequest,
) -> DepartmentResponse:
    """
    Creates a new department for a hospital.

    Validates:
      - Department name is unique within this hospital
        (two hospitals CAN have a Cardiology dept — uniqueness is per-hospital)
      - If head_of_department_id is provided, that user exists
        and belongs to the same hospital

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token — scopes this to one hospital
        data:        Department details from the request body

    Returns:
        DepartmentResponse
    """

    # Check name uniqueness within this hospital
    # Note: we check within hospital_id scope, not globally
    existing = await db.execute(
        select(Department).where(
            Department.hospital_id == hospital_id,
            Department.name == data.name,
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictException(
            f"A department named '{data.name}' already exists in your hospital."
        )

    # Validate head of department if provided
    if data.head_of_department_id:
        hod_result = await db.execute(
            select(HospitalUser).where(
                HospitalUser.id == data.head_of_department_id,
                HospitalUser.hospital_id == hospital_id,  # Must belong to same hospital
            )
        )
        if not hod_result.scalar_one_or_none():
            raise NotFoundException("Head of department user")

    # Create the department
    department = Department(
        hospital_id=hospital_id,
        name=data.name,
        code=data.code.upper() if data.code else None,
        description=data.description,
        head_of_department_id=data.head_of_department_id,
        is_active=True,
    )
    db.add(department)
    await db.commit()
    await db.refresh(department)

    return DepartmentResponse.model_validate(department)


async def list_departments(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 50,
    active_only: bool = True,
) -> DepartmentListResponse:
    """
    Lists all departments for a hospital.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token
        page:        Page number
        per_page:    Results per page
        active_only: If True, only return active departments

    Returns:
        DepartmentListResponse
    """
    query = (
        select(Department)
        .where(Department.hospital_id == hospital_id)
        .order_by(Department.name)
    )

    if active_only:
        query = query.where(Department.is_active == True)

    # Count total
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    departments = result.scalars().all()

    return DepartmentListResponse(
        items=[DepartmentResponse.model_validate(d) for d in departments],
        total=total,
        page=page,
        per_page=per_page,
    )


async def get_department(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID,
) -> DepartmentResponse:
    """
    Fetches a single department by ID.

    The hospital_id filter ensures a hospital can only fetch
    its own departments — not another hospital's.
    """
    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            Department.hospital_id == hospital_id,
        )
    )
    department = result.scalar_one_or_none()

    if not department:
        raise NotFoundException("Department")

    return DepartmentResponse.model_validate(department)


async def update_department(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID,
    data: DepartmentUpdateRequest,
) -> DepartmentResponse:
    """
    Updates a department's details.

    Only updates fields that were actually provided in the request.
    Fields not included in the request body are left unchanged.
    """
    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            Department.hospital_id == hospital_id,
        )
    )
    department = result.scalar_one_or_none()

    if not department:
        raise NotFoundException("Department")

    # Check name uniqueness if name is being changed
    if data.name and data.name != department.name:
        existing = await db.execute(
            select(Department).where(
                Department.hospital_id == hospital_id,
                Department.name == data.name,
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictException(
                f"A department named '{data.name}' already exists."
            )

    # Validate new head of department if provided
    if data.head_of_department_id:
        hod_result = await db.execute(
            select(HospitalUser).where(
                HospitalUser.id == data.head_of_department_id,
                HospitalUser.hospital_id == hospital_id,
            )
        )
        if not hod_result.scalar_one_or_none():
            raise NotFoundException("Head of department user")

    # Apply only the fields that were provided
    if data.name is not None:
        department.name = data.name
    if data.code is not None:
        department.code = data.code.upper()
    if data.description is not None:
        department.description = data.description
    if data.head_of_department_id is not None:
        department.head_of_department_id = data.head_of_department_id
    if data.is_active is not None:
        department.is_active = data.is_active

    await db.commit()
    await db.refresh(department)

    return DepartmentResponse.model_validate(department)


async def delete_department(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID,
) -> dict:
    """
    Soft-deletes a department by setting is_active to False.

    We never hard-delete departments because staff members may
    reference them in historical records. Deactivation hides them
    from active views while preserving data integrity.
    """
    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            Department.hospital_id == hospital_id,
        )
    )
    department = result.scalar_one_or_none()

    if not department:
        raise NotFoundException("Department")

    department.is_active = False
    await db.commit()

    return {"message": f"Department '{department.name}' has been deactivated."}