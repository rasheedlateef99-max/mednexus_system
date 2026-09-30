"""
Laboratory Order Router
--------------------------
HTTP endpoints for laboratory test ordering and result management.

Access control:
  - Order tests:        Clinical staff (doctor, nurse, admin)
  - Collect specimen:   Any active staff (nurse, lab scientist, admin)
  - Enter result:       Lab scientist or admin only
  - Review order:       Doctor or admin only
  - Cancel:              Ordering doctor or admin only
  - View / queue / history: All active staff

Route order: /queue and /patient-history before /{order_id}.

Endpoints:
  POST   /hospital/lab-orders/                               → Order tests
  GET    /hospital/lab-orders/                                → List orders
  GET    /hospital/lab-orders/queue                           → Lab queue
  GET    /hospital/lab-orders/patient-history/{pid}           → Patient history
  GET    /hospital/lab-orders/{id}                            → Get order
  POST   /hospital/lab-orders/{id}/collect-specimen           → Record collection
  POST   /hospital/lab-orders/{id}/items/{iid}/result         → Enter result
  POST   /hospital/lab-orders/{id}/review                     → Doctor review
  POST   /hospital/lab-orders/{id}/cancel                     → Cancel order
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user, require_clinical_staff
from app.schemas.hospital.lab_order import (
    LabOrderCreateRequest,
    SpecimenCollectionRequest,
    LabResultEntryRequest,
    LabOrderReviewRequest,
    LabOrderCancelRequest,
    LabOrderResponse,
    LabOrderListResponse,
    PatientLabHistoryResponse,
)
from app.services.hospital.lab_order_service import (
    order_lab_tests,
    get_lab_order,
    collect_specimen,
    enter_result,
    review_lab_order,
    cancel_lab_order,
    list_lab_orders,
    get_lab_queue,
    get_patient_lab_history,
)

router = APIRouter()


@router.post(
    "/",
    response_model=LabOrderResponse,
    status_code=201,
    summary="Order Laboratory Tests",
    description="Creates a new lab order with one or more requested tests. Requires clinical staff role.",
)
async def order(
    data: LabOrderCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_clinical_staff),
):
    return await order_lab_tests(db, ctx["hospital_id"], ctx["user"], data)


@router.get(
    "/queue",
    response_model=LabOrderListResponse,
    summary="Lab Working Queue",
    description="Returns orders with collected/processing samples, ordered by priority then age. Primary view for lab scientists.",
)
async def queue(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_lab_queue(db, ctx["hospital_id"], page, per_page)


@router.get(
    "/patient-history/{patient_id}",
    response_model=PatientLabHistoryResponse,
    summary="Patient Lab History",
    description="Returns all lab orders for a patient, most recent first.",
)
async def patient_history(
    patient_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_patient_lab_history(db, ctx["hospital_id"], patient_id)


@router.get(
    "/",
    response_model=LabOrderListResponse,
    summary="List Lab Orders",
    description="Returns a paginated list of lab orders with optional filters.",
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    patient_id: UUID | None = Query(default=None),
    doctor_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_lab_orders(
        db, ctx["hospital_id"], page, per_page, patient_id, doctor_id, status, priority,
    )


@router.get(
    "/{order_id}",
    response_model=LabOrderResponse,
    summary="Get Lab Order",
    description="Returns full lab order details including all test items and results.",
)
async def get_one(
    order_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_lab_order(db, ctx["hospital_id"], order_id)


@router.post(
    "/{order_id}/collect-specimen",
    response_model=LabOrderResponse,
    summary="Record Specimen Collection",
    description="Records that a specimen has been collected. Moves order to sample_collected status.",
)
async def collect(
    order_id: UUID,
    data: SpecimenCollectionRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await collect_specimen(db, ctx["hospital_id"], order_id, data, ctx["user"])


@router.post(
    "/{order_id}/items/{item_id}/result",
    response_model=LabOrderResponse,
    summary="Enter Test Result",
    description="Enters a result for a single test item. Lab scientist only. Auto-flags abnormal results when numeric bounds are provided.",
)
async def result(
    order_id: UUID,
    item_id: UUID,
    data: LabResultEntryRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await enter_result(db, ctx["hospital_id"], order_id, item_id, data, ctx["user"])


@router.post(
    "/{order_id}/review",
    response_model=LabOrderResponse,
    summary="Doctor Review / Sign-off",
    description="Doctor reviews completed results and adds clinical comments. Doctor or admin only.",
)
async def review(
    order_id: UUID,
    data: LabOrderReviewRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await review_lab_order(db, ctx["hospital_id"], order_id, data, ctx["user"])


@router.post(
    "/{order_id}/cancel",
    response_model=LabOrderResponse,
    summary="Cancel Lab Order",
    description="Cancels an order that has not been completed. Ordering doctor or admin only.",
)
async def cancel(
    order_id: UUID,
    data: LabOrderCancelRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await cancel_lab_order(db, ctx["hospital_id"], order_id, data, ctx["user"])