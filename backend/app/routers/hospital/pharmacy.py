"""
Pharmacy Inventory Router
-----------------------------
HTTP endpoints for pharmacy catalogue and stock management.

Access control:
  - Create/update catalogue items, receive stock, adjust stock: pharmacist or admin
  - View items, alerts: all active staff

Route order: /alerts before /{item_id}.

Endpoints:
  POST   /hospital/pharmacy/items                      → Add catalogue item
  GET    /hospital/pharmacy/items                       → List items
  GET    /hospital/pharmacy/alerts                       → Low stock / expiry alerts
  GET    /hospital/pharmacy/items/{id}                   → Item detail with batches
  PUT    /hospital/pharmacy/items/{id}                   → Update item
  POST   /hospital/pharmacy/items/{id}/receive           → Receive new stock batch
  POST   /hospital/pharmacy/items/{id}/adjust            → Manual stock adjustment
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user
from app.schemas.hospital.pharmacy import (
    PharmacyItemCreateRequest,
    PharmacyItemUpdateRequest,
    StockReceiveRequest,
    StockAdjustmentRequest,
    PharmacyItemResponse,
    PharmacyItemDetailResponse,
    PharmacyItemListResponse,
    PharmacyAlertsResponse,
)
from app.services.hospital.pharmacy_service import (
    create_pharmacy_item,
    update_pharmacy_item,
    get_pharmacy_item,
    list_pharmacy_items,
    receive_stock,
    adjust_stock,
    get_pharmacy_alerts,
)

router = APIRouter()


@router.post(
    "/items",
    response_model=PharmacyItemResponse,
    status_code=201,
    summary="Add Catalogue Item",
    description="Adds a new drug to the pharmacy catalogue. Pharmacist or admin only.",
)
async def create_item(
    data: PharmacyItemCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await create_pharmacy_item(db, ctx["hospital_id"], data, ctx["user"])


@router.get(
    "/alerts",
    response_model=PharmacyAlertsResponse,
    summary="Pharmacy Alerts",
    description="Returns low-stock items and near-expiry batches for the pharmacy dashboard.",
)
async def alerts(
    near_expiry_days: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_pharmacy_alerts(db, ctx["hospital_id"], near_expiry_days)


@router.get(
    "/items",
    response_model=PharmacyItemListResponse,
    summary="List Catalogue Items",
    description="Returns a paginated, searchable list of pharmacy catalogue items.",
)
async def list_items(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    low_stock_only: bool = Query(default=False),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_pharmacy_items(
        db, ctx["hospital_id"], page, per_page, search, category, low_stock_only, active_only,
    )


@router.get(
    "/items/{item_id}",
    response_model=PharmacyItemDetailResponse,
    summary="Get Catalogue Item",
    description="Returns full item detail including all stock batches.",
)
async def get_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_pharmacy_item(db, ctx["hospital_id"], item_id)


@router.put(
    "/items/{item_id}",
    response_model=PharmacyItemResponse,
    summary="Update Catalogue Item",
    description="Updates catalogue metadata. Does not directly affect stock quantities.",
)
async def update_item(
    item_id: UUID,
    data: PharmacyItemUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await update_pharmacy_item(db, ctx["hospital_id"], item_id, data, ctx["user"])


@router.post(
    "/items/{item_id}/receive",
    response_model=PharmacyItemDetailResponse,
    summary="Receive Stock",
    description="Records a new batch of stock received from a supplier.",
)
async def receive(
    item_id: UUID,
    data: StockReceiveRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await receive_stock(db, ctx["hospital_id"], item_id, data, ctx["user"])


@router.post(
    "/items/{item_id}/adjust",
    response_model=PharmacyItemDetailResponse,
    summary="Adjust Stock",
    description="Manually adjusts stock for wastage, loss, damage, or count correction.",
)
async def adjust(
    item_id: UUID,
    data: StockAdjustmentRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await adjust_stock(db, ctx["hospital_id"], item_id, data, ctx["user"])