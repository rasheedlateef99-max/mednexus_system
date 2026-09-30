"""
Prescriptions Router
----------------------
HTTP endpoints for digital prescription management.

Access control:
  - Issue prescription:   Clinical staff (doctor, nurse, admin)
  - View prescription:    All active hospital staff
  - List prescriptions:   All active hospital staff
  - Pharmacy queue:       All active hospital staff (primarily pharmacists)
  - Dispense item:        Pharmacist or hospital admin only
  - Cancel:               Issuing doctor or hospital admin only
  - Patient history:      All active hospital staff

Route order:
  Specific paths (/queue, /patient-history) before /{prescription_id}
  to prevent FastAPI path parameter collision.

Endpoints:
  POST   /hospital/prescriptions/                          → Issue prescription
  GET    /hospital/prescriptions/                          → List prescriptions
  GET    /hospital/prescriptions/queue                     → Pharmacy queue
  GET    /hospital/prescriptions/patient-history/{pid}     → Patient history
  GET    /hospital/prescriptions/{id}                      → Get prescription
  POST   /hospital/prescriptions/{id}/items/{iid}/dispense → Dispense item
  POST   /hospital/prescriptions/{id}/cancel               → Cancel prescription
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_tenant_db
from app.core.dependencies import get_current_hospital_user, require_clinical_staff
from app.schemas.hospital.prescription import (
    PrescriptionCreateRequest,
    PrescriptionCancelRequest,
    PrescriptionItemDispenseRequest,
    PrescriptionResponse,
    PrescriptionListResponse,
    PatientPrescriptionHistoryResponse,
)
from app.services.hospital.prescription_service import (
    issue_prescription,
    get_prescription,
    list_prescriptions,
    dispense_item,
    cancel_prescription,
    get_patient_prescription_history,
    get_pharmacy_queue,
)

router = APIRouter()


@router.post(
    "/",
    response_model=PrescriptionResponse,
    status_code=201,
    summary="Issue Prescription",
    description=(
        "Issues a new prescription with one or more drug items. "
        "Requires clinical staff role. "
        "Creates the prescription header and all items atomically."
    ),
)
async def issue(
    data: PrescriptionCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(require_clinical_staff),
):
    return await issue_prescription(
        db=db,
        hospital_id=ctx["hospital_id"],
        issuing_user=ctx["user"],
        data=data,
    )


@router.get(
    "/queue",
    response_model=PrescriptionListResponse,
    summary="Pharmacy Dispensing Queue",
    description=(
        "Returns all prescriptions awaiting dispensing (issued or dispensing status). "
        "Ordered oldest first (FIFO — first written, first dispensed). "
        "This is the primary view for pharmacists."
    ),
)
async def pharmacy_queue(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_pharmacy_queue(
        db=db,
        hospital_id=ctx["hospital_id"],
        page=page,
        per_page=per_page,
    )


@router.get(
    "/patient-history/{patient_id}",
    response_model=PatientPrescriptionHistoryResponse,
    summary="Patient Prescription History",
    description=(
        "Returns all prescriptions for a patient, most recent first. "
        "Includes expiry status. "
        "Available to all active hospital staff."
    ),
)
async def patient_history(
    patient_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_patient_prescription_history(
        db=db,
        hospital_id=ctx["hospital_id"],
        patient_id=patient_id,
    )


@router.get(
    "/",
    response_model=PrescriptionListResponse,
    summary="List Prescriptions",
    description=(
        "Returns a paginated list of prescriptions. "
        "Supports filtering by patient, doctor, and status."
    ),
)
async def list_all(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    patient_id: UUID | None = Query(default=None),
    doctor_id: UUID | None = Query(default=None),
    status: str | None = Query(
        default=None,
        description=(
            "issued | dispensing | dispensed | "
            "partially_dispensed | cancelled | expired"
        ),
    ),
    include_expired: bool = Query(
        default=False,
        description="Include expired prescriptions in results",
    ),
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await list_prescriptions(
        db=db,
        hospital_id=ctx["hospital_id"],
        page=page,
        per_page=per_page,
        patient_id=patient_id,
        doctor_id=doctor_id,
        status_filter=status,
        include_expired=include_expired,
    )


@router.get(
    "/{prescription_id}",
    response_model=PrescriptionResponse,
    summary="Get Prescription",
    description=(
        "Returns full prescription details including all drug items. "
        "Applies expiry check on every read."
    ),
)
async def get_one(
    prescription_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await get_prescription(db, ctx["hospital_id"], prescription_id)


@router.post(
    "/{prescription_id}/items/{item_id}/dispense",
    response_model=PrescriptionResponse,
    summary="Dispense Prescription Item",
    description=(
        "Records dispensing of a single drug item. "
        "Pharmacist only. "
        "After updating, automatically recomputes the prescription's "
        "overall status from all items."
    ),
)
async def dispense(
    prescription_id: UUID,
    item_id: UUID,
    data: PrescriptionItemDispenseRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await dispense_item(
        db=db,
        hospital_id=ctx["hospital_id"],
        prescription_id=prescription_id,
        item_id=item_id,
        data=data,
        dispensing_user=ctx["user"],
    )


@router.post(
    "/{prescription_id}/cancel",
    response_model=PrescriptionResponse,
    summary="Cancel Prescription",
    description=(
        "Cancels a prescription that has not been dispensed. "
        "Issuing doctor or hospital admin only. "
        "Cancellation reason is required. "
        "All pending items are automatically cancelled."
    ),
)
async def cancel(
    prescription_id: UUID,
    data: PrescriptionCancelRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: dict = Depends(get_current_hospital_user),
):
    return await cancel_prescription(
        db=db,
        hospital_id=ctx["hospital_id"],
        prescription_id=prescription_id,
        data=data,
        requesting_user=ctx["user"],
    )