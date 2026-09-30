"""
Pharmacy Inventory Schemas
------------------------------
Request and response shapes for pharmacy stock management.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime, date


# ── Pharmacy Item Schemas ─────────────────────────────────────────────────────

class PharmacyItemCreateRequest(BaseModel):
    """Body for adding a new drug to the pharmacy catalogue."""
    drug_name: str = Field(..., min_length=1, max_length=255)
    generic_name: str | None = Field(None, max_length=255)
    strength: str | None = Field(None, max_length=100)
    dosage_form: str | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=100)
    unit: str = Field(default="units", max_length=50)
    reorder_level: int = Field(default=10, ge=0)
    reorder_quantity: int = Field(default=50, ge=1)
    unit_cost: float | None = Field(None, ge=0)
    unit_price: float | None = Field(None, ge=0)


class PharmacyItemUpdateRequest(BaseModel):
    """Body for updating catalogue metadata. All fields optional."""
    drug_name: str | None = Field(None, min_length=1, max_length=255)
    generic_name: str | None = Field(None, max_length=255)
    strength: str | None = Field(None, max_length=100)
    dosage_form: str | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=100)
    unit: str | None = Field(None, max_length=50)
    reorder_level: int | None = Field(None, ge=0)
    reorder_quantity: int | None = Field(None, ge=1)
    unit_cost: float | None = Field(None, ge=0)
    unit_price: float | None = Field(None, ge=0)
    is_active: bool | None = None


class PharmacyItemResponse(BaseModel):
    """Full pharmacy item with computed stock flags."""
    id: UUID
    hospital_id: UUID
    drug_name: str
    generic_name: str | None
    strength: str | None
    dosage_form: str | None
    category: str | None
    unit: str
    total_quantity: int
    reorder_level: int
    reorder_quantity: int
    unit_cost: float | None
    unit_price: float | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Computed
    is_low_stock: bool = False
    nearest_expiry: date | None = None
    has_near_expiry_batch: bool = False

    model_config = {"from_attributes": True}


class PharmacyItemListResponse(BaseModel):
    items: list[PharmacyItemResponse]
    total: int
    page: int
    per_page: int
    pages: int


# ── Stock Batch Schemas ───────────────────────────────────────────────────────

class StockReceiveRequest(BaseModel):
    """Body for recording newly received stock."""
    quantity_received: int = Field(..., ge=1)
    expiry_date: date = Field(..., description="Format: YYYY-MM-DD")
    batch_number: str | None = Field(None, max_length=100)
    supplier_name: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=500)

    @field_validator("expiry_date")
    @classmethod
    def validate_expiry(cls, v):
        if v <= date.today():
            raise ValueError("Expiry date must be in the future")
        return v


class StockAdjustmentRequest(BaseModel):
    """
    Body for manually adjusting stock (wastage, loss, damage, recount).
    Adjustment can be positive (found extra stock) or negative (lost/damaged).
    """
    quantity_change: int = Field(
        ...,
        description="Positive to increase stock, negative to decrease",
    )
    reason: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Reason for the adjustment — required for audit trail",
    )

    @field_validator("quantity_change")
    @classmethod
    def validate_nonzero(cls, v):
        if v == 0:
            raise ValueError("quantity_change cannot be zero")
        return v


class StockBatchResponse(BaseModel):
    """A single stock batch."""
    id: UUID
    hospital_id: UUID
    pharmacy_item_id: UUID
    batch_number: str | None
    supplier_name: str | None
    quantity_received: int
    quantity_remaining: int
    expiry_date: date
    received_at: datetime
    received_by: UUID | None
    notes: str | None

    # Computed
    is_expired: bool = False
    is_near_expiry: bool = False

    model_config = {"from_attributes": True}


class PharmacyItemDetailResponse(PharmacyItemResponse):
    """Pharmacy item with all its stock batches."""
    batches: list[StockBatchResponse] = []


# ── Alerts / Reporting ────────────────────────────────────────────────────────

class LowStockAlertItem(BaseModel):
    id: UUID
    drug_name: str
    strength: str | None
    total_quantity: int
    reorder_level: int
    reorder_quantity: int


class ExpiryAlertItem(BaseModel):
    batch_id: UUID
    pharmacy_item_id: UUID
    drug_name: str
    strength: str | None
    quantity_remaining: int
    expiry_date: date
    days_until_expiry: int


class PharmacyAlertsResponse(BaseModel):
    low_stock_count: int
    near_expiry_count: int
    expired_batch_count: int
    low_stock_items: list[LowStockAlertItem]
    near_expiry_batches: list[ExpiryAlertItem]