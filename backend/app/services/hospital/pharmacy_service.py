"""
Pharmacy Inventory Service
------------------------------
Business logic for drug catalogue management, stock receiving,
FEFO-based dispensing deduction, manual adjustments, and alerts.

FEFO Deduction Algorithm (_deduct_stock_fefo):
  1. Fetch all batches for the item with quantity_remaining > 0
     and expiry_date >= today (expired batches are never dispensed from)
  2. Order by expiry_date ascending (soonest-expiring first)
  3. Walk through batches, deducting as much as possible from each
     until the requested quantity is fully covered
  4. If total available across all valid batches is insufficient,
     raise an error BEFORE deducting anything (all-or-nothing —
     no partial silent deduction)
  5. Update PharmacyItem.total_quantity to reflect the new total

This function is called both directly (pharmacy router, for testing/
manual stock-out) and from prescription_service.py during dispensing.

Near-Expiry Window:
  Configurable per call; default 90 days. A batch is "near expiry"
  if 0 <= (expiry_date - today).days <= window, and not yet expired.
"""

import uuid
from datetime import date, datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.models.tenant.pharmacy import (
    PharmacyItem,
    PharmacyStockBatch,
    StockMovementType,
)
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.schemas.hospital.pharmacy import (
    PharmacyItemCreateRequest,
    PharmacyItemUpdateRequest,
    StockReceiveRequest,
    StockAdjustmentRequest,
    PharmacyItemResponse,
    PharmacyItemDetailResponse,
    PharmacyItemListResponse,
    StockBatchResponse,
    PharmacyAlertsResponse,
    LowStockAlertItem,
    ExpiryAlertItem,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    ForbiddenException,
)

NEAR_EXPIRY_DEFAULT_DAYS = 90


# ── Computed Field Helpers ────────────────────────────────────────────────────

def _is_near_expiry(expiry_date: date, window_days: int = NEAR_EXPIRY_DEFAULT_DAYS) -> bool:
    today = date.today()
    if expiry_date < today:
        return False  # already expired, not "near" expiry
    return (expiry_date - today).days <= window_days


def _is_expired(expiry_date: date) -> bool:
    return expiry_date < date.today()


async def _get_batches(db: AsyncSession, pharmacy_item_id: uuid.UUID) -> list[StockBatchResponse]:
    result = await db.execute(
        select(PharmacyStockBatch)
        .where(PharmacyStockBatch.pharmacy_item_id == pharmacy_item_id)
        .order_by(PharmacyStockBatch.expiry_date.asc())
    )
    batches = result.scalars().all()
    responses = []
    for b in batches:
        r = StockBatchResponse.model_validate(b)
        r.is_expired = _is_expired(b.expiry_date)
        r.is_near_expiry = _is_near_expiry(b.expiry_date)
        responses.append(r)
    return responses


async def _build_item_response(db: AsyncSession, item: PharmacyItem) -> PharmacyItemResponse:
    response = PharmacyItemResponse.model_validate(item)
    response.is_low_stock = item.total_quantity <= item.reorder_level

    batch_result = await db.execute(
        select(PharmacyStockBatch)
        .where(
            PharmacyStockBatch.pharmacy_item_id == item.id,
            PharmacyStockBatch.quantity_remaining > 0,
        )
        .order_by(PharmacyStockBatch.expiry_date.asc())
    )
    batches = batch_result.scalars().all()
    if batches:
        response.nearest_expiry = batches[0].expiry_date
        response.has_near_expiry_batch = any(
            _is_near_expiry(b.expiry_date) for b in batches
        )
    return response


# ── Recompute Total Quantity (denormalized field) ─────────────────────────────

async def _recompute_total_quantity(db: AsyncSession, pharmacy_item_id: uuid.UUID) -> None:
    """
    Recalculates PharmacyItem.total_quantity from non-expired batches.
    Called after every stock movement (receive, dispense, adjust).
    """
    today = date.today()
    result = await db.execute(
        select(func.coalesce(func.sum(PharmacyStockBatch.quantity_remaining), 0))
        .where(
            PharmacyStockBatch.pharmacy_item_id == pharmacy_item_id,
            PharmacyStockBatch.expiry_date >= today,
        )
    )
    new_total = result.scalar() or 0

    item_result = await db.execute(
        select(PharmacyItem).where(PharmacyItem.id == pharmacy_item_id)
    )
    item = item_result.scalar_one_or_none()
    if item:
        item.total_quantity = new_total
        await db.commit()


# ── FEFO Deduction (used by dispensing) ───────────────────────────────────────

async def deduct_stock_fefo(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    pharmacy_item_id: uuid.UUID,
    quantity: int,
) -> bool:
    """
    Deducts `quantity` units from the item's stock, oldest-expiring batch first.

    All-or-nothing: if total available stock (non-expired batches) is less
    than `quantity`, raises ValidationException and deducts NOTHING.

    Returns True on success.

    This is the function prescription_service.py calls during dispensing.
    """
    today = date.today()
    result = await db.execute(
        select(PharmacyStockBatch)
        .where(
            PharmacyStockBatch.hospital_id == hospital_id,
            PharmacyStockBatch.pharmacy_item_id == pharmacy_item_id,
            PharmacyStockBatch.quantity_remaining > 0,
            PharmacyStockBatch.expiry_date >= today,
        )
        .order_by(PharmacyStockBatch.expiry_date.asc())
    )
    batches = result.scalars().all()

    available = sum(b.quantity_remaining for b in batches)
    if available < quantity:
        raise ValidationException(
            f"Insufficient stock. Requested {quantity}, but only "
            f"{available} units available (excluding expired batches)."
        )

    remaining_to_deduct = quantity
    for batch in batches:
        if remaining_to_deduct <= 0:
            break
        deduct_from_this_batch = min(batch.quantity_remaining, remaining_to_deduct)
        batch.quantity_remaining -= deduct_from_this_batch
        remaining_to_deduct -= deduct_from_this_batch

    await db.commit()
    await _recompute_total_quantity(db, pharmacy_item_id)
    return True


async def find_pharmacy_item_by_drug_name(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    drug_name: str,
) -> PharmacyItem | None:
    """
    Best-effort lookup of a catalogue item by drug name (case-insensitive,
    exact match on name). Used by prescription dispensing to check whether
    a prescribed drug has a tracked inventory entry.

    Returns None if not found — this is expected and handled gracefully
    by the caller, since not every prescribed drug must exist in the
    formal catalogue.
    """
    result = await db.execute(
        select(PharmacyItem).where(
            PharmacyItem.hospital_id == hospital_id,
            func.lower(PharmacyItem.drug_name) == drug_name.lower(),
            PharmacyItem.is_active == True,
        )
    )
    return result.scalar_one_or_none()


# ── Core Service Functions ────────────────────────────────────────────────────

async def create_pharmacy_item(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    data: PharmacyItemCreateRequest,
    requesting_user: HospitalUser,
) -> PharmacyItemResponse:
    """Adds a new drug to the pharmacy catalogue. Pharmacist or admin only."""
    if requesting_user.role not in {HospitalUserRole.PHARMACIST, HospitalUserRole.HOSPITAL_ADMIN}:
        raise ForbiddenException("Only pharmacists can manage the pharmacy catalogue.")

    existing = await db.execute(
        select(PharmacyItem).where(
            PharmacyItem.hospital_id == hospital_id,
            func.lower(PharmacyItem.drug_name) == data.drug_name.lower(),
            PharmacyItem.strength == data.strength,
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictException(
            f"'{data.drug_name}' ({data.strength or 'no strength specified'}) "
            f"already exists in the catalogue."
        )

    item = PharmacyItem(
        hospital_id=hospital_id,
        drug_name=data.drug_name,
        generic_name=data.generic_name,
        strength=data.strength,
        dosage_form=data.dosage_form,
        category=data.category,
        unit=data.unit,
        reorder_level=data.reorder_level,
        reorder_quantity=data.reorder_quantity,
        unit_cost=data.unit_cost,
        unit_price=data.unit_price,
        total_quantity=0,
        is_active=True,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    return await _build_item_response(db, item)


async def update_pharmacy_item(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    item_id: uuid.UUID,
    data: PharmacyItemUpdateRequest,
    requesting_user: HospitalUser,
) -> PharmacyItemResponse:
    """Updates catalogue metadata. Does not affect stock quantities directly."""
    if requesting_user.role not in {HospitalUserRole.PHARMACIST, HospitalUserRole.HOSPITAL_ADMIN}:
        raise ForbiddenException("Only pharmacists can manage the pharmacy catalogue.")

    result = await db.execute(
        select(PharmacyItem).where(
            PharmacyItem.id == item_id, PharmacyItem.hospital_id == hospital_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise NotFoundException("Pharmacy item")

    for field in (
        "drug_name", "generic_name", "strength", "dosage_form", "category",
        "unit", "reorder_level", "reorder_quantity", "unit_cost", "unit_price",
        "is_active",
    ):
        value = getattr(data, field)
        if value is not None:
            setattr(item, field, value)

    await db.commit()
    await db.refresh(item)
    return await _build_item_response(db, item)


async def get_pharmacy_item(
    db: AsyncSession, hospital_id: uuid.UUID, item_id: uuid.UUID,
) -> PharmacyItemDetailResponse:
    """Returns a single pharmacy item with all its stock batches."""
    result = await db.execute(
        select(PharmacyItem).where(
            PharmacyItem.id == item_id, PharmacyItem.hospital_id == hospital_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise NotFoundException("Pharmacy item")

    base = await _build_item_response(db, item)
    detail = PharmacyItemDetailResponse(**base.model_dump())
    detail.batches = await _get_batches(db, item.id)
    return detail


async def list_pharmacy_items(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    search: str | None = None,
    category: str | None = None,
    low_stock_only: bool = False,
    active_only: bool = True,
) -> PharmacyItemListResponse:
    """Returns a paginated, searchable list of catalogue items."""
    query = select(PharmacyItem).where(PharmacyItem.hospital_id == hospital_id)

    if active_only:
        query = query.where(PharmacyItem.is_active == True)
    if search:
        term = f"%{search}%"
        query = query.where(PharmacyItem.drug_name.ilike(term))
    if category:
        query = query.where(PharmacyItem.category == category)
    if low_stock_only:
        query = query.where(PharmacyItem.total_quantity <= PharmacyItem.reorder_level)

    query = query.order_by(PharmacyItem.drug_name.asc())

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    db_items = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    items = [await _build_item_response(db, i) for i in db_items]

    return PharmacyItemListResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)


async def receive_stock(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    item_id: uuid.UUID,
    data: StockReceiveRequest,
    receiving_user: HospitalUser,
) -> PharmacyItemDetailResponse:
    """Records a new batch of stock received from a supplier."""
    if receiving_user.role not in {HospitalUserRole.PHARMACIST, HospitalUserRole.HOSPITAL_ADMIN}:
        raise ForbiddenException("Only pharmacists can receive stock.")

    item_result = await db.execute(
        select(PharmacyItem).where(
            PharmacyItem.id == item_id, PharmacyItem.hospital_id == hospital_id,
        )
    )
    item = item_result.scalar_one_or_none()
    if not item:
        raise NotFoundException("Pharmacy item")

    batch = PharmacyStockBatch(
        hospital_id=hospital_id,
        pharmacy_item_id=item.id,
        batch_number=data.batch_number,
        supplier_name=data.supplier_name,
        quantity_received=data.quantity_received,
        quantity_remaining=data.quantity_received,
        expiry_date=data.expiry_date,
        received_by=receiving_user.id,
        notes=data.notes,
    )
    db.add(batch)
    await db.commit()

    await _recompute_total_quantity(db, item.id)
    await db.refresh(item)

    base = await _build_item_response(db, item)
    detail = PharmacyItemDetailResponse(**base.model_dump())
    detail.batches = await _get_batches(db, item.id)
    return detail


async def adjust_stock(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    item_id: uuid.UUID,
    data: StockAdjustmentRequest,
    adjusting_user: HospitalUser,
) -> PharmacyItemDetailResponse:
    """
    Manually adjusts stock up or down (wastage, loss, damage, stock count correction).

    For increases: adds a new batch with a far-future placeholder expiry
    is NOT used — instead, an increase requires the pharmacist to have
    already received stock via receive_stock(). This function only
    INCREASES the most recently expiring active batch, or DECREASES
    proportionally from soonest-expiring batches first (same FEFO order
    as dispensing), since adjustments represent corrections to existing
    physical stock, not new deliveries.
    """
    if adjusting_user.role not in {HospitalUserRole.PHARMACIST, HospitalUserRole.HOSPITAL_ADMIN}:
        raise ForbiddenException("Only pharmacists can adjust stock.")

    item_result = await db.execute(
        select(PharmacyItem).where(
            PharmacyItem.id == item_id, PharmacyItem.hospital_id == hospital_id,
        )
    )
    item = item_result.scalar_one_or_none()
    if not item:
        raise NotFoundException("Pharmacy item")

    today = date.today()
    batches_result = await db.execute(
        select(PharmacyStockBatch)
        .where(
            PharmacyStockBatch.pharmacy_item_id == item.id,
            PharmacyStockBatch.expiry_date >= today,
        )
        .order_by(PharmacyStockBatch.expiry_date.asc())
    )
    batches = batches_result.scalars().all()

    if data.quantity_change > 0:
        # Increase: apply to the batch with the LATEST expiry (most likely
        # to be the most recently received / least depleted batch context)
        if not batches:
            raise ValidationException(
                "Cannot increase stock — no active batches exist for this item. "
                "Use the receive stock endpoint to add a new batch instead."
            )
        target = max(batches, key=lambda b: b.expiry_date)
        target.quantity_remaining += data.quantity_change
    else:
        # Decrease: deduct FEFO, same as dispensing, but allow it to fail
        # gracefully with a clear message if insufficient stock exists
        decrease_amount = abs(data.quantity_change)
        available = sum(b.quantity_remaining for b in batches)
        if available < decrease_amount:
            raise ValidationException(
                f"Cannot decrease stock by {decrease_amount} — only "
                f"{available} units currently available."
            )
        remaining = decrease_amount
        for batch in batches:
            if remaining <= 0:
                break
            take = min(batch.quantity_remaining, remaining)
            batch.quantity_remaining -= take
            remaining -= take

    await db.commit()
    await _recompute_total_quantity(db, item.id)
    await db.refresh(item)

    base = await _build_item_response(db, item)
    detail = PharmacyItemDetailResponse(**base.model_dump())
    detail.batches = await _get_batches(db, item.id)
    return detail


async def get_pharmacy_alerts(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    near_expiry_days: int = NEAR_EXPIRY_DEFAULT_DAYS,
) -> PharmacyAlertsResponse:
    """Returns low-stock and near-expiry alerts for the pharmacy dashboard."""
    today = date.today()
    horizon = today + timedelta(days=near_expiry_days)

    low_stock_result = await db.execute(
        select(PharmacyItem).where(
            PharmacyItem.hospital_id == hospital_id,
            PharmacyItem.is_active == True,
            PharmacyItem.total_quantity <= PharmacyItem.reorder_level,
        )
    )
    low_stock_items = low_stock_result.scalars().all()

    near_expiry_result = await db.execute(
        select(PharmacyStockBatch, PharmacyItem)
        .join(PharmacyItem, PharmacyStockBatch.pharmacy_item_id == PharmacyItem.id)
        .where(
            PharmacyStockBatch.hospital_id == hospital_id,
            PharmacyStockBatch.quantity_remaining > 0,
            PharmacyStockBatch.expiry_date >= today,
            PharmacyStockBatch.expiry_date <= horizon,
        )
        .order_by(PharmacyStockBatch.expiry_date.asc())
    )
    near_expiry_rows = near_expiry_result.all()

    expired_count_result = await db.execute(
        select(func.count(PharmacyStockBatch.id)).where(
            PharmacyStockBatch.hospital_id == hospital_id,
            PharmacyStockBatch.quantity_remaining > 0,
            PharmacyStockBatch.expiry_date < today,
        )
    )
    expired_count = expired_count_result.scalar() or 0

    return PharmacyAlertsResponse(
        low_stock_count=len(low_stock_items),
        near_expiry_count=len(near_expiry_rows),
        expired_batch_count=expired_count,
        low_stock_items=[
            LowStockAlertItem(
                id=i.id, drug_name=i.drug_name, strength=i.strength,
                total_quantity=i.total_quantity, reorder_level=i.reorder_level,
                reorder_quantity=i.reorder_quantity,
            )
            for i in low_stock_items
        ],
        near_expiry_batches=[
            ExpiryAlertItem(
                batch_id=batch.id,
                pharmacy_item_id=item.id,
                drug_name=item.drug_name,
                strength=item.strength,
                quantity_remaining=batch.quantity_remaining,
                expiry_date=batch.expiry_date,
                days_until_expiry=(batch.expiry_date - today).days,
            )
            for batch, item in near_expiry_rows
        ],
    )