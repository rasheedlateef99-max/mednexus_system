"""
Laboratory Order Service
---------------------------
Business logic for laboratory test ordering, specimen tracking,
result entry, and doctor review.

Status aggregation logic mirrors the prescription pattern:
  All items completed     → completed
  All items cancelled     → cancelled
  Some completed, none pending → partially_completed
  At least one pending    → processing (if collected) or ordered

Automatic abnormal detection:
  If numeric_value is provided along with reference_low/reference_high,
  is_abnormal is computed automatically unless explicitly overridden.

M20 hook:
  enter_result() fires a CRITICAL_LAB_RESULT notification to the
  ordering doctor when item.is_critical is True. Notification failure
  never raises — result entry always completes regardless.
"""

import uuid
import re
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.tenant.lab_order import (
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabOrderItemStatus,
    LabOrderPriority,
)
from app.models.tenant.patient import Patient
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.schemas.hospital.lab_order import (
    LabOrderCreateRequest,
    SpecimenCollectionRequest,
    LabResultEntryRequest,
    LabOrderReviewRequest,
    LabOrderCancelRequest,
    LabOrderResponse,
    LabOrderSummary,
    LabOrderListResponse,
    PatientLabHistoryResponse,
    LabOrderItemResponse,
)
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    ForbiddenException,
)


# ── Order Number Generation ───────────────────────────────────────────────────

async def _generate_order_number(db: AsyncSession, hospital_id: uuid.UUID) -> str:
    """Generates sequential LAB-XXXXX number per hospital."""
    result = await db.execute(
        select(LabOrder.order_number)
        .where(LabOrder.hospital_id == hospital_id)
        .order_by(LabOrder.created_at.desc())
    )
    existing = result.scalars().all()
    max_number = 0
    for number_str in existing:
        match = re.search(r"LAB-(\d+)", number_str)
        if match:
            num = int(match.group(1))
            if num > max_number:
                max_number = num
    return f"LAB-{max_number + 1:05d}"


# ── Status Aggregation ────────────────────────────────────────────────────────

async def _recompute_order_status(db: AsyncSession, order: LabOrder) -> LabOrder:
    """Recomputes the order's overall status from its items."""
    result = await db.execute(
        select(LabOrderItem).where(LabOrderItem.lab_order_id == order.id)
    )
    items = result.scalars().all()
    if not items:
        return order

    statuses = [item.status for item in items]
    all_completed = all(s == LabOrderItemStatus.COMPLETED for s in statuses)
    all_cancelled = all(s == LabOrderItemStatus.CANCELLED for s in statuses)
    any_pending = any(s == LabOrderItemStatus.PENDING for s in statuses)
    any_completed = any(s == LabOrderItemStatus.COMPLETED for s in statuses)

    if all_completed:
        order.status = LabOrderStatus.COMPLETED
    elif all_cancelled:
        order.status = LabOrderStatus.CANCELLED
    elif not any_pending and any_completed:
        order.status = LabOrderStatus.PARTIALLY_COMPLETED
    elif not any_pending and not any_completed:
        order.status = LabOrderStatus.PARTIALLY_COMPLETED
    elif order.specimen_collected_at:
        order.status = LabOrderStatus.PROCESSING
    else:
        order.status = LabOrderStatus.ORDERED

    await db.commit()
    await db.refresh(order)
    return order


# ── Name Enrichment ───────────────────────────────────────────────────────────

async def _get_patient_name(db, patient_id, hospital_id) -> str:
    result = await db.execute(
        select(Patient.first_name, Patient.last_name).where(
            Patient.id == patient_id, Patient.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return f"{row.first_name} {row.last_name}" if row else ""


async def _get_user_name(db, user_id, hospital_id) -> str:
    result = await db.execute(
        select(HospitalUser.full_name).where(
            HospitalUser.id == user_id, HospitalUser.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return row.full_name if row else ""


async def _get_items(db, lab_order_id) -> list[LabOrderItemResponse]:
    result = await db.execute(
        select(LabOrderItem)
        .where(LabOrderItem.lab_order_id == lab_order_id)
        .order_by(LabOrderItem.created_at.asc())
    )
    items = result.scalars().all()
    return [LabOrderItemResponse.model_validate(i) for i in items]


async def _build_response(db, order: LabOrder, hospital_id) -> LabOrderResponse:
    response = LabOrderResponse.model_validate(order)
    response.patient_name = await _get_patient_name(db, order.patient_id, hospital_id)
    response.doctor_name = await _get_user_name(db, order.doctor_id, hospital_id)
    response.items = await _get_items(db, order.id)
    return response


async def _build_summary(db, order: LabOrder, hospital_id) -> LabOrderSummary:
    summary = LabOrderSummary.model_validate(order)
    summary.patient_name = await _get_patient_name(db, order.patient_id, hospital_id)
    summary.doctor_name = await _get_user_name(db, order.doctor_id, hospital_id)

    items_result = await db.execute(
        select(LabOrderItem).where(LabOrderItem.lab_order_id == order.id)
    )
    items = items_result.scalars().all()
    summary.item_count = len(items)
    summary.has_abnormal_results = any(i.is_abnormal for i in items)
    summary.has_critical_results = any(i.is_critical for i in items)
    return summary


# ── Core Service Functions ────────────────────────────────────────────────────

async def order_lab_tests(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    ordering_user: HospitalUser,
    data: LabOrderCreateRequest,
) -> LabOrderResponse:
    """Creates a new lab order with its test items."""
    clinical_roles = {
        HospitalUserRole.HOSPITAL_ADMIN,
        HospitalUserRole.DOCTOR,
        HospitalUserRole.NURSE,
    }
    if ordering_user.role not in clinical_roles:
        raise ForbiddenException("Only clinical staff can order laboratory tests.")

    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == data.patient_id,
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    if not patient_result.scalar_one_or_none():
        raise NotFoundException("Patient")

    doctor_result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == data.doctor_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    if not doctor_result.scalar_one_or_none():
        raise NotFoundException("Doctor")

    order_number = await _generate_order_number(db, hospital_id)

    order = LabOrder(
        hospital_id=hospital_id,
        patient_id=data.patient_id,
        doctor_id=data.doctor_id,
        medical_record_id=data.medical_record_id,
        appointment_id=data.appointment_id,
        order_number=order_number,
        clinical_indication=data.clinical_indication,
        priority=LabOrderPriority(data.priority),
        status=LabOrderStatus.ORDERED,
    )
    db.add(order)
    await db.flush()

    for item_data in data.items:
        item = LabOrderItem(
            hospital_id=hospital_id,
            lab_order_id=order.id,
            test_name=item_data.test_name,
            test_category=item_data.test_category,
            status=LabOrderItemStatus.PENDING,
        )
        db.add(item)

    await db.commit()
    await db.refresh(order)

    return await _build_response(db, order, hospital_id)


async def get_lab_order(
    db: AsyncSession, hospital_id: uuid.UUID, order_id: uuid.UUID,
) -> LabOrderResponse:
    """Fetches a single lab order with all items."""
    result = await db.execute(
        select(LabOrder).where(
            LabOrder.id == order_id, LabOrder.hospital_id == hospital_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundException("Lab order")
    return await _build_response(db, order, hospital_id)


async def collect_specimen(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    order_id: uuid.UUID,
    data: SpecimenCollectionRequest,
    collecting_user: HospitalUser,
) -> LabOrderResponse:
    """
    Records specimen collection for an order.
    Moves status from 'ordered' to 'sample_collected'.
    """
    result = await db.execute(
        select(LabOrder).where(
            LabOrder.id == order_id, LabOrder.hospital_id == hospital_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundException("Lab order")

    if order.status != LabOrderStatus.ORDERED:
        raise ValidationException(
            f"Cannot collect specimen for an order with status '{order.status.value}'. "
            f"Specimen collection is only valid for newly ordered tests."
        )

    order.specimen_collected_at = datetime.now(timezone.utc)
    order.specimen_collected_by = collecting_user.id
    order.specimen_type = data.specimen_type
    order.status = LabOrderStatus.SAMPLE_COLLECTED

    await db.commit()
    await db.refresh(order)

    return await _build_response(db, order, hospital_id)


async def enter_result(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    data: LabResultEntryRequest,
    lab_scientist: HospitalUser,
) -> LabOrderResponse:
    """
    Enters a result for a single test item.
    Lab scientists only.
    Automatically computes is_abnormal if numeric bounds are provided
    and not manually overridden.
    Fires a CRITICAL_LAB_RESULT notification to the ordering doctor
    if item.is_critical is True (M20 hook).
    """
    if lab_scientist.role not in {
        HospitalUserRole.LAB_SCIENTIST,
        HospitalUserRole.HOSPITAL_ADMIN,
    }:
        raise ForbiddenException(
            "Only lab scientists can enter laboratory results."
        )

    order_result = await db.execute(
        select(LabOrder).where(
            LabOrder.id == order_id, LabOrder.hospital_id == hospital_id,
        )
    )
    order = order_result.scalar_one_or_none()
    if not order:
        raise NotFoundException("Lab order")

    if order.status == LabOrderStatus.CANCELLED:
        raise ValidationException("Cannot enter results for a cancelled order.")

    item_result = await db.execute(
        select(LabOrderItem).where(
            LabOrderItem.id == item_id,
            LabOrderItem.lab_order_id == order_id,
            LabOrderItem.hospital_id == hospital_id,
        )
    )
    item = item_result.scalar_one_or_none()
    if not item:
        raise NotFoundException("Lab order item")

    if item.status != LabOrderItemStatus.PENDING:
        raise ValidationException(
            f"This test item is already '{item.status.value}'."
        )

    # Determine abnormal flag
    is_abnormal = data.is_abnormal
    if is_abnormal is None and data.numeric_value is not None:
        if data.reference_low is not None and data.numeric_value < data.reference_low:
            is_abnormal = True
        elif data.reference_high is not None and data.numeric_value > data.reference_high:
            is_abnormal = True
        else:
            is_abnormal = False
    elif is_abnormal is None:
        is_abnormal = False

    item.result_value = data.result_value
    item.numeric_value = data.numeric_value
    item.unit = data.unit
    item.reference_range = data.reference_range
    item.reference_low = data.reference_low
    item.reference_high = data.reference_high
    item.is_abnormal = is_abnormal
    item.is_critical = data.is_critical
    item.lab_notes = data.lab_notes
    item.status = LabOrderItemStatus.COMPLETED
    item.resulted_by = lab_scientist.id
    item.resulted_at = datetime.now(timezone.utc)

    await db.commit()

    # ── M20 hook: notify ordering doctor on critical result ───────────────────
    if item.is_critical:
        try:
            from app.services.hospital.notification_service import (
                create_notification,
                NotificationEventType,
            )
            await create_notification(
                db=db,
                hospital_id=hospital_id,
                recipient_id=order.doctor_id,
                event_type=NotificationEventType.CRITICAL_LAB_RESULT,
                title="Critical Lab Result",
                body=(
                    f"A critical result has been recorded for test "
                    f"'{item.test_name}' on order {order.order_number}. "
                    f"Immediate review is required."
                ),
                link_path=f"/lab-orders/{order.id}",
                source_type="lab_order",
                source_id=order.id,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to send critical lab result notification for order %s",
                order.order_number,
                exc_info=True,
            )
    # ─────────────────────────────────────────────────────────────────────────

    order = await _recompute_order_status(db, order)

    return await _build_response(db, order, hospital_id)


async def review_lab_order(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    order_id: uuid.UUID,
    data: LabOrderReviewRequest,
    reviewing_doctor: HospitalUser,
) -> LabOrderResponse:
    """
    Doctor reviews and signs off on a completed lab order.
    Only valid on orders with status 'completed' or 'partially_completed'.
    """
    if reviewing_doctor.role not in {
        HospitalUserRole.DOCTOR,
        HospitalUserRole.HOSPITAL_ADMIN,
    }:
        raise ForbiddenException("Only doctors can review laboratory results.")

    result = await db.execute(
        select(LabOrder).where(
            LabOrder.id == order_id, LabOrder.hospital_id == hospital_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundException("Lab order")

    if order.status not in {
        LabOrderStatus.COMPLETED,
        LabOrderStatus.PARTIALLY_COMPLETED,
    }:
        raise ValidationException(
            f"Cannot review an order with status '{order.status.value}'. "
            f"Results must be completed before review."
        )

    order.reviewed_by = reviewing_doctor.id
    order.reviewed_at = datetime.now(timezone.utc)
    order.doctor_comments = data.doctor_comments

    await db.commit()
    await db.refresh(order)

    return await _build_response(db, order, hospital_id)


async def cancel_lab_order(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    order_id: uuid.UUID,
    data: LabOrderCancelRequest,
    requesting_user: HospitalUser,
) -> LabOrderResponse:
    """Cancels a lab order. Issuing doctor or admin only."""
    result = await db.execute(
        select(LabOrder).where(
            LabOrder.id == order_id, LabOrder.hospital_id == hospital_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundException("Lab order")

    if order.status == LabOrderStatus.COMPLETED:
        raise ValidationException("Cannot cancel a completed order.")
    if order.status == LabOrderStatus.CANCELLED:
        raise ValidationException("This order is already cancelled.")

    is_issuing_doctor = requesting_user.id == order.doctor_id
    is_admin = requesting_user.role == HospitalUserRole.HOSPITAL_ADMIN
    if not is_issuing_doctor and not is_admin:
        raise ForbiddenException(
            "Only the ordering doctor or a hospital administrator can cancel this order."
        )

    order.status = LabOrderStatus.CANCELLED
    order.cancelled_at = datetime.now(timezone.utc)
    order.cancellation_reason = data.reason
    order.cancelled_by = requesting_user.id

    items_result = await db.execute(
        select(LabOrderItem).where(
            LabOrderItem.lab_order_id == order_id,
            LabOrderItem.status == LabOrderItemStatus.PENDING,
        )
    )
    for item in items_result.scalars().all():
        item.status = LabOrderItemStatus.CANCELLED

    await db.commit()
    await db.refresh(order)

    return await _build_response(db, order, hospital_id)


async def list_lab_orders(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    priority_filter: str | None = None,
) -> LabOrderListResponse:
    """Returns a paginated list of lab orders."""
    query = (
        select(LabOrder)
        .where(LabOrder.hospital_id == hospital_id)
        .order_by(LabOrder.created_at.desc())
    )

    if patient_id:
        query = query.where(LabOrder.patient_id == patient_id)
    if doctor_id:
        query = query.where(LabOrder.doctor_id == doctor_id)
    if status_filter:
        try:
            query = query.where(LabOrder.status == LabOrderStatus(status_filter))
        except ValueError:
            pass
    if priority_filter:
        try:
            query = query.where(LabOrder.priority == LabOrderPriority(priority_filter))
        except ValueError:
            pass

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    orders = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    items = [await _build_summary(db, o, hospital_id) for o in orders]

    return LabOrderListResponse(
        items=items, total=total, page=page, per_page=per_page, pages=pages,
    )


async def get_lab_queue(
    db: AsyncSession, hospital_id: uuid.UUID, page: int = 1, per_page: int = 20,
) -> LabOrderListResponse:
    """
    Returns the lab scientist's working queue:
    orders that are sample_collected or processing, oldest first.
    """
    query = (
        select(LabOrder)
        .where(
            LabOrder.hospital_id == hospital_id,
            LabOrder.status.in_([
                LabOrderStatus.SAMPLE_COLLECTED,
                LabOrderStatus.PROCESSING,
            ]),
        )
        .order_by(LabOrder.priority.desc(), LabOrder.created_at.asc())
    )

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    orders = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    items = [await _build_summary(db, o, hospital_id) for o in orders]

    return LabOrderListResponse(
        items=items, total=total, page=page, per_page=per_page, pages=pages,
    )


async def get_patient_lab_history(
    db: AsyncSession, hospital_id: uuid.UUID, patient_id: uuid.UUID,
) -> PatientLabHistoryResponse:
    """Returns all lab orders for one patient, most recent first."""
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id, Patient.hospital_id == hospital_id,
        )
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise NotFoundException("Patient")

    result = await db.execute(
        select(LabOrder)
        .where(LabOrder.patient_id == patient_id, LabOrder.hospital_id == hospital_id)
        .order_by(LabOrder.created_at.desc())
    )
    orders = result.scalars().all()
    summaries = [await _build_summary(db, o, hospital_id) for o in orders]

    return PatientLabHistoryResponse(
        patient_id=patient.id,
        patient_name=patient.full_name,
        total_orders=len(summaries),
        orders=summaries,
    )