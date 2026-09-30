"""
Hospital Department Router
-----------------------------
HTTP endpoints for department management.

All routes are scoped to the authenticated hospital via hospital_id
extracted from the JWT token through the get_current_hospital_user dependency.

Endpoints:
  POST   /hospital/departments/              → Create department
  GET    /hospital/departments/              → List departments
  GET    /hospital/departments/{id}          → Get single department
  PUT    /hospital/departments/{id}          → Update department
  DELETE /hospital/departments/{id}          → Deactivate department
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user, require_hospital_admin
from app.schemas.hospital.department import (
    DepartmentCreateRequest,
    DepartmentUpdateRequest,
    DepartmentResponse,
    DepartmentListResponse,
)
from app.services.hospital.department_service import (
    create_department,
    list_departments,
    get_department,
    update_department,
    delete_department,
)

router = APIRouter()


@router.post(
    "/",
    response_model=DepartmentResponse,
    summary="Create Department",
    description="Creates a new department. Requires hospital admin role.",
)
async def create(
    data: DepartmentCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_hospital_admin),
):
    return await create_department(db, ctx["hospital_id"], data)


@router.get(
    "/",
    response_model=DepartmentListResponse,
    summary="List Departments",
    description="Returns all departments for the authenticated hospital.",
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_departments(db, ctx["hospital_id"], page, per_page, active_only)


@router.get(
    "/{department_id}",
    response_model=DepartmentResponse,
    summary="Get Department",
    description="Returns a single department by ID.",
)
async def get_one(
    department_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_department(db, ctx["hospital_id"], department_id)


@router.put(
    "/{department_id}",
    response_model=DepartmentResponse,
    summary="Update Department",
    description="Updates department details. Requires hospital admin role.",
)
async def update(
    department_id: UUID,
    data: DepartmentUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_hospital_admin),
):
    return await update_department(db, ctx["hospital_id"], department_id, data)


@router.delete(
    "/{department_id}",
    summary="Deactivate Department",
    description="Soft-deactivates a department. Requires hospital admin role.",
)
async def deactivate(
    department_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_hospital_admin),
):
    result = await delete_department(db, ctx["hospital_id"], department_id)
    return {"success": True, "data": result}