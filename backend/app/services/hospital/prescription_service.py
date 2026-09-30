"""
Prescription Service
----------------------
Business logic for digital prescription management.

Key responsibilities:
  1. Prescription number generation  — sequential per hospital (RX-00001)
  2. Prescription issuance           — creates header + items atomically
  3. Expiry computation              — calculates expires_on on creation
  4. Expiry checking                 — marks expired on access (lazy)
  5. Pharmacist dispensing           — per-item status updates + STOCK DEDUCTION
  6. Status aggregation              — derives prescription status from items
  7. Cancellation                    — voiding with reason
  8. Patient history                 — chronological prescription list

Status Aggregation Logic:
  After any item is updated, the prescription's overall status
  is recomputed from its items:

    All items dispensed               → dispensed
    All items cancelled               → cancelled
    Some dispensed, none pending      → partially_dispensed
    At least one pending              → dispensing (if started) or issued
    All not_available or cancelled    → partially_dispensed

Multi-tenancy:
  Every function receives hospital_id from the JWT token.
  All queries filter by hospital_id.
  Doctors and patients are validated to belong to the same hospital.

Pharmacy Inventory Integration (Milestone 10):
  When an item is dispensed with status='dispensed', the service
  attempts to find a matching catalogue entry by drug name and
  deduct stock using FEFO (First Expired, First Out) ordering.

    - Match found, sufficient stock  → stock deducted, dispense proceeds
    - Match found, insufficient stock → dispense BLOCKED with clear error,
                                          nothing is written
    - No catalogue match found        → dispense proceeds anyway, a note
                                          is appended to dispensing_notes
                                          (not every prescribed drug needs
                                          a formal inventory entry yet)

  Stock deduction only happens when status == "dispensed". Marking an
  item "not_available" or "cancelled" never touches inventory.
"""

import uuid
import re
from datetime import datetime, timezone, date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.tenant.prescription import (
    Prescription,
    PrescriptionItem,
    PrescriptionStatus,
    PrescriptionItemStatus,
)
from app.models.tenant.patient import Patient
from app.models.tenant.hospital_user import HospitalUser, HospitalUserRole
from app.schemas.hospital.prescription import (
    PrescriptionCreateRequest,
    PrescriptionCancelRequest,
    PrescriptionItemDispenseRequest,
    PrescriptionResponse,
    PrescriptionSummary,
    PrescriptionListResponse,
    PatientPrescriptionHistoryResponse,
    PrescriptionItemResponse,
)
from app.core.exceptions import (
    NotFoundException,
    ConflictException,
    ValidationException,
    ForbiddenException,
)

# ── Pharmacy Inventory Integration (Milestone 10) ─────────────────────────────
from app.services.hospital.pharmacy_service import (
    find_pharmacy_item_by_drug_name,
    deduct_stock_fefo,
)


# ── Prescription Number Generation ───────────────────────────────────────────

async def _generate_prescription_number(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> str:
    """
    Generates the next sequential prescription number for a hospital.

    Format: RX-XXXXX (zero-padded to 5 digits)
    Examples: RX-00001, RX-00042, RX-10000

    Follows the same pattern as patient number generation.

    Args:
        db:          Tenant database session
        hospital_id: The hospital to generate the number for

    Returns:
        A string like "RX-00001"
    """
    result = await db.execute(
        select(Prescription.prescription_number)
        .where(Prescription.hospital_id == hospital_id)
        .order_by(Prescription.created_at.desc())
    )
    existing_numbers = result.scalars().all()

    max_number = 0
    for number_str in existing_numbers:
        match = re.search(r"RX-(\d+)", number_str)
        if match:
            num = int(match.group(1))
            if num > max_number:
                max_number = num

    next_number = max_number + 1
    return f"RX-{next_number:05d}"


# ── Expiry Check ──────────────────────────────────────────────────────────────

async def _check_and_apply_expiry(
    db: AsyncSession,
    prescription: Prescription,
) -> Prescription:
    """
    Lazily marks a prescription as expired if its validity period has passed.

    Only applies to prescriptions in 'issued' or 'dispensing' status.
    Terminal statuses (dispensed, cancelled, expired) are not changed.

    Args:
        db:           Tenant database session
        prescription: The prescription to check

    Returns:
        The prescription (status updated to 'expired' if applicable)
    """
    if prescription.status not in (
        PrescriptionStatus.ISSUED,
        PrescriptionStatus.DISPENSING,
    ):
        return prescription

    if prescription.expires_on is None:
        return prescription

    today = date.today()
    if today > prescription.expires_on:
        prescription.status = PrescriptionStatus.EXPIRED
        await db.commit()
        await db.refresh(prescription)

    return prescription


# ── Status Aggregation ────────────────────────────────────────────────────────

async def _recompute_prescription_status(
    db: AsyncSession,
    prescription: Prescription,
) -> Prescription:
    """
    Recomputes the prescription's overall status from its item statuses.

    Called after any item is updated by the pharmacist.

    Aggregation rules:
      All items dispensed               → dispensed
      All items cancelled               → cancelled
      All items not_available/cancelled → partially_dispensed
      Some items dispensed, none pending → partially_dispensed
      At least one item pending         → dispensing

    Args:
        db:           Tenant database session
        prescription: The prescription to recompute

    Returns:
        Updated prescription
    """
    result = await db.execute(
        select(PrescriptionItem).where(
            PrescriptionItem.prescription_id == prescription.id
        )
    )
    items = result.scalars().all()

    if not items:
        return prescription

    statuses = [item.status for item in items]

    all_dispensed = all(s == PrescriptionItemStatus.DISPENSED for s in statuses)
    all_cancelled = all(s == PrescriptionItemStatus.CANCELLED for s in statuses)
    any_pending = any(s == PrescriptionItemStatus.PENDING for s in statuses)
    any_dispensed = any(s == PrescriptionItemStatus.DISPENSED for s in statuses)

    if all_dispensed:
        prescription.status = PrescriptionStatus.DISPENSED
        prescription.dispensed_at = datetime.now(timezone.utc)
    elif all_cancelled:
        prescription.status = PrescriptionStatus.CANCELLED
    elif not any_pending and any_dispensed:
        prescription.status = PrescriptionStatus.PARTIALLY_DISPENSED
    elif not any_pending and not any_dispensed:
        # All not_available or cancelled
        prescription.status = PrescriptionStatus.PARTIALLY_DISPENSED
    else:
        # At least one pending — in progress
        prescription.status = PrescriptionStatus.DISPENSING

    await db.commit()
    await db.refresh(prescription)
    return prescription


# ── Name Enrichment ───────────────────────────────────────────────────────────

async def _get_patient_name(
    db: AsyncSession,
    patient_id: uuid.UUID,
    hospital_id: uuid.UUID,
) -> str:
    result = await db.execute(
        select(Patient.first_name, Patient.last_name).where(
            Patient.id == patient_id,
            Patient.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return f"{row.first_name} {row.last_name}" if row else ""


async def _get_user_name(
    db: AsyncSession,
    user_id: uuid.UUID,
    hospital_id: uuid.UUID,
) -> str:
    result = await db.execute(
        select(HospitalUser.full_name).where(
            HospitalUser.id == user_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    row = result.first()
    return row.full_name if row else ""


async def _get_items(
    db: AsyncSession,
    prescription_id: uuid.UUID,
) -> list[PrescriptionItemResponse]:
    """Fetches all items for a prescription."""
    result = await db.execute(
        select(PrescriptionItem)
        .where(PrescriptionItem.prescription_id == prescription_id)
        .order_by(PrescriptionItem.created_at.asc())
    )
    items = result.scalars().all()
    return [PrescriptionItemResponse.model_validate(item) for item in items]


async def _build_response(
    db: AsyncSession,
    prescription: Prescription,
    hospital_id: uuid.UUID,
) -> PrescriptionResponse:
    """Builds a full PrescriptionResponse with items and names."""
    response = PrescriptionResponse.model_validate(prescription)
    response.patient_name = await _get_patient_name(
        db, prescription.patient_id, hospital_id
    )
    response.doctor_name = await _get_user_name(
        db, prescription.doctor_id, hospital_id
    )
    if prescription.dispensed_by:
        response.dispensed_by_name = await _get_user_name(
            db, prescription.dispensed_by, hospital_id
        )
    response.items = await _get_items(db, prescription.id)
    return response


async def _build_summary(
    db: AsyncSession,
    prescription: Prescription,
    hospital_id: uuid.UUID,
) -> PrescriptionSummary:
    """Builds a compact PrescriptionSummary with item count."""
    summary = PrescriptionSummary.model_validate(prescription)
    summary.patient_name = await _get_patient_name(
        db, prescription.patient_id, hospital_id
    )
    summary.doctor_name = await _get_user_name(
        db, prescription.doctor_id, hospital_id
    )
    # Count items
    count_result = await db.execute(
        select(func.count(PrescriptionItem.id)).where(
            PrescriptionItem.prescription_id == prescription.id
        )
    )
    summary.item_count = count_result.scalar() or 0
    return summary


# ── Core Service Functions ────────────────────────────────────────────────────

async def issue_prescription(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    issuing_user: HospitalUser,
    data: PrescriptionCreateRequest,
) -> PrescriptionResponse:
    """
    Issues a new prescription with its drug items.

    Validates:
      - Issuing user is clinical staff
      - Patient exists and belongs to this hospital
      - Doctor exists and belongs to this hospital
      - At least one item is provided

    Creates both the prescription header and all items atomically.

    Args:
        db:           Tenant database session
        hospital_id:  From the JWT token
        issuing_user: The staff member issuing the prescription
        data:         Prescription and item details

    Returns:
        PrescriptionResponse with all items
    """

    # Validate issuing user is clinical staff
    clinical_roles = {
        HospitalUserRole.HOSPITAL_ADMIN,
        HospitalUserRole.DOCTOR,
        HospitalUserRole.NURSE,
    }
    if issuing_user.role not in clinical_roles:
        raise ForbiddenException(
            "Only clinical staff can issue prescriptions."
        )

    # Validate patient
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == data.patient_id,
            Patient.hospital_id == hospital_id,
            Patient.is_active == True,
        )
    )
    if not patient_result.scalar_one_or_none():
        raise NotFoundException("Patient")

    # Validate doctor
    doctor_result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == data.doctor_id,
            HospitalUser.hospital_id == hospital_id,
        )
    )
    if not doctor_result.scalar_one_or_none():
        raise NotFoundException("Doctor")

    # Generate prescription number
    prescription_number = await _generate_prescription_number(db, hospital_id)

    # Compute expiry date
    today = date.today()
    expires_on = today + timedelta(days=data.validity_days)

    # Create prescription header
    prescription = Prescription(
        hospital_id=hospital_id,
        patient_id=data.patient_id,
        doctor_id=data.doctor_id,
        medical_record_id=data.medical_record_id,
        appointment_id=data.appointment_id,
        prescription_number=prescription_number,
        pharmacist_notes=data.pharmacist_notes,
        diagnosis_note=data.diagnosis_note,
        validity_days=data.validity_days,
        expires_on=expires_on,
        status=PrescriptionStatus.ISSUED,
    )
    db.add(prescription)
    await db.flush()  # Get the prescription ID before creating items

    # Create all items
    for item_data in data.items:
        item = PrescriptionItem(
            hospital_id=hospital_id,
            prescription_id=prescription.id,
            drug_name=item_data.drug_name,
            generic_name=item_data.generic_name,
            strength=item_data.strength,
            dosage_form=item_data.dosage_form,
            dose=item_data.dose,
            frequency=item_data.frequency,
            duration=item_data.duration,
            route=item_data.route,
            instructions=item_data.instructions,
            quantity_prescribed=item_data.quantity_prescribed,
            quantity_unit=item_data.quantity_unit,
            quantity_dispensed=0,
            status=PrescriptionItemStatus.PENDING,
        )
        db.add(item)

    await db.commit()
    await db.refresh(prescription)

    return await _build_response(db, prescription, hospital_id)


async def get_prescription(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    prescription_id: uuid.UUID,
) -> PrescriptionResponse:
    """
    Fetches a single prescription with all its items.

    Applies expiry check on every read.

    Args:
        db:              Tenant database session
        hospital_id:     From the JWT token
        prescription_id: UUID of the prescription

    Returns:
        PrescriptionResponse

    Raises:
        NotFoundException if not found or belongs to another hospital
    """
    result = await db.execute(
        select(Prescription).where(
            Prescription.id == prescription_id,
            Prescription.hospital_id == hospital_id,
        )
    )
    prescription = result.scalar_one_or_none()

    if not prescription:
        raise NotFoundException("Prescription")

    prescription = await _check_and_apply_expiry(db, prescription)
    return await _build_response(db, prescription, hospital_id)


async def list_prescriptions(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    include_expired: bool = False,
) -> PrescriptionListResponse:
    """
    Returns a paginated list of prescriptions.

    Ordered by created_at descending (most recent first).

    Args:
        db:             Tenant database session
        hospital_id:    From the JWT token
        page:           Page number
        per_page:       Results per page
        patient_id:     Filter by patient
        doctor_id:      Filter by doctor
        status_filter:  Filter by status string
        include_expired: Whether to include expired prescriptions

    Returns:
        PrescriptionListResponse
    """
    query = (
        select(Prescription)
        .where(Prescription.hospital_id == hospital_id)
        .order_by(Prescription.created_at.desc())
    )

    if patient_id:
        query = query.where(Prescription.patient_id == patient_id)

    if doctor_id:
        query = query.where(Prescription.doctor_id == doctor_id)

    if status_filter:
        try:
            status_enum = PrescriptionStatus(status_filter)
            query = query.where(Prescription.status == status_enum)
        except ValueError:
            pass

    if not include_expired:
        query = query.where(
            Prescription.status != PrescriptionStatus.EXPIRED
        )

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    prescriptions = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    items = []
    for p in prescriptions:
        p = await _check_and_apply_expiry(db, p)
        items.append(await _build_summary(db, p, hospital_id))

    return PrescriptionListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


async def dispense_item(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    prescription_id: uuid.UUID,
    item_id: uuid.UUID,
    data: PrescriptionItemDispenseRequest,
    dispensing_user: HospitalUser,
) -> PrescriptionResponse:
    """
    Records dispensing of a single prescription item.

    Only pharmacists (and hospital admins) can dispense.

    PHARMACY INVENTORY INTEGRATION (Milestone 10):
    When status == "dispensed", attempts to deduct stock from the
    matching pharmacy catalogue item (matched by drug name) using
    FEFO ordering. If no catalogue match exists, dispensing proceeds
    with a note. If a match exists but stock is insufficient, the
    entire dispense attempt is blocked — nothing is written to either
    the prescription item or the inventory.

    After updating the item, recomputes the prescription's overall
    status from all items.

    If all items are dispensed → prescription becomes 'dispensed'.
    If some pending → prescription becomes 'dispensing'.
    If none pending, some dispensed → 'partially_dispensed'.

    Args:
        db:              Tenant database session
        hospital_id:     From the JWT token
        prescription_id: UUID of the prescription
        item_id:         UUID of the specific item to dispense
        data:            Dispensing details
        dispensing_user: Must be pharmacist or admin

    Returns:
        Updated PrescriptionResponse with all items
    """

    # Only pharmacists and admins can dispense
    can_dispense = dispensing_user.role in {
        HospitalUserRole.PHARMACIST,
        HospitalUserRole.HOSPITAL_ADMIN,
    }
    if not can_dispense:
        raise ForbiddenException(
            "Only pharmacists can dispense prescriptions."
        )

    # Load the prescription
    prx_result = await db.execute(
        select(Prescription).where(
            Prescription.id == prescription_id,
            Prescription.hospital_id == hospital_id,
        )
    )
    prescription = prx_result.scalar_one_or_none()

    if not prescription:
        raise NotFoundException("Prescription")

    # Check prescription is not in a terminal state
    terminal_statuses = {
        PrescriptionStatus.DISPENSED,
        PrescriptionStatus.CANCELLED,
        PrescriptionStatus.EXPIRED,
    }
    if prescription.status in terminal_statuses:
        raise ValidationException(
            f"Cannot dispense from a {prescription.status.value} prescription."
        )

    # Load the specific item
    item_result = await db.execute(
        select(PrescriptionItem).where(
            PrescriptionItem.id == item_id,
            PrescriptionItem.prescription_id == prescription_id,
            PrescriptionItem.hospital_id == hospital_id,
        )
    )
    item = item_result.scalar_one_or_none()

    if not item:
        raise NotFoundException("Prescription item")

    if item.status != PrescriptionItemStatus.PENDING:
        raise ValidationException(
            f"This item is already '{item.status.value}' and cannot be updated."
        )

    # ── Pharmacy Inventory Check (Milestone 10) ───────────────────────────────
    # Only relevant when the pharmacist is actually marking this item dispensed.
    # 'not_available' and 'cancelled' never touch inventory.
    stock_note = None
    if data.status == "dispensed":
        pharmacy_item = await find_pharmacy_item_by_drug_name(
            db, hospital_id, item.drug_name
        )

        if pharmacy_item:
            # Match found — attempt FEFO deduction.
            # deduct_stock_fefo raises ValidationException if stock is
            # insufficient, which propagates up and blocks this dispense
            # entirely. Nothing below this line executes if it raises.
            quantity_to_deduct = data.quantity_dispensed
            if quantity_to_deduct > 0:
                await deduct_stock_fefo(
                    db, hospital_id, pharmacy_item.id, quantity_to_deduct
                )
        else:
            # No catalogue entry for this drug — proceed anyway, but
            # record that inventory was not tracked for this dispense.
            stock_note = (
                f"Note: '{item.drug_name}' is not in the pharmacy catalogue. "
                f"Dispensed without inventory deduction."
            )

    # Update the item
    item.quantity_dispensed = data.quantity_dispensed
    item.status = PrescriptionItemStatus(data.status)
    combined_notes = data.dispensing_notes or ""
    if stock_note:
        combined_notes = f"{combined_notes}\n{stock_note}".strip()
    item.dispensing_notes = combined_notes or None
    item.substituted = data.substituted
    item.substitution_drug = data.substitution_drug

    # Record who is dispensing on the prescription header
    if not prescription.dispensed_by:
        prescription.dispensed_by = dispensing_user.id

    await db.commit()

    # Recompute overall prescription status from all items
    prescription = await _recompute_prescription_status(db, prescription)

    return await _build_response(db, prescription, hospital_id)


async def cancel_prescription(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    prescription_id: uuid.UUID,
    data: PrescriptionCancelRequest,
    requesting_user: HospitalUser,
) -> PrescriptionResponse:
    """
    Cancels a prescription that has not yet been dispensed.

    Only the issuing doctor or a hospital admin can cancel.
    Cannot cancel prescriptions that are already dispensed or expired.

    Args:
        db:               Tenant database session
        hospital_id:      From the JWT token
        prescription_id:  UUID of the prescription to cancel
        data:             Cancellation reason
        requesting_user:  Must be issuing doctor or admin

    Returns:
        Updated PrescriptionResponse
    """
    result = await db.execute(
        select(Prescription).where(
            Prescription.id == prescription_id,
            Prescription.hospital_id == hospital_id,
        )
    )
    prescription = result.scalar_one_or_none()

    if not prescription:
        raise NotFoundException("Prescription")

    # Cannot cancel terminal states
    if prescription.status == PrescriptionStatus.DISPENSED:
        raise ValidationException(
            "Cannot cancel a prescription that has already been dispensed."
        )
    if prescription.status == PrescriptionStatus.CANCELLED:
        raise ValidationException("This prescription is already cancelled.")

    # Only the issuing doctor or admin can cancel
    is_issuing_doctor = requesting_user.id == prescription.doctor_id
    is_admin = requesting_user.role == HospitalUserRole.HOSPITAL_ADMIN
    if not is_issuing_doctor and not is_admin:
        raise ForbiddenException(
            "Only the prescribing doctor or a hospital administrator "
            "can cancel a prescription."
        )

    now = datetime.now(timezone.utc)
    prescription.status = PrescriptionStatus.CANCELLED
    prescription.cancelled_at = now
    prescription.cancellation_reason = data.reason
    prescription.cancelled_by = requesting_user.id

    # Cancel all pending items
    items_result = await db.execute(
        select(PrescriptionItem).where(
            PrescriptionItem.prescription_id == prescription_id,
            PrescriptionItem.status == PrescriptionItemStatus.PENDING,
        )
    )
    pending_items = items_result.scalars().all()
    for item in pending_items:
        item.status = PrescriptionItemStatus.CANCELLED

    await db.commit()
    await db.refresh(prescription)

    return await _build_response(db, prescription, hospital_id)


async def get_patient_prescription_history(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> PatientPrescriptionHistoryResponse:
    """
    Returns all prescriptions for one patient, most recent first.

    Applies expiry check on each prescription during the read.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token
        patient_id:  UUID of the patient

    Returns:
        PatientPrescriptionHistoryResponse
    """
    # Verify patient exists in this hospital
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.hospital_id == hospital_id,
        )
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise NotFoundException("Patient")

    result = await db.execute(
        select(Prescription)
        .where(
            Prescription.patient_id == patient_id,
            Prescription.hospital_id == hospital_id,
        )
        .order_by(Prescription.created_at.desc())
    )
    prescriptions = result.scalars().all()

    summaries = []
    for p in prescriptions:
        p = await _check_and_apply_expiry(db, p)
        summaries.append(await _build_summary(db, p, hospital_id))

    return PatientPrescriptionHistoryResponse(
        patient_id=patient.id,
        patient_name=patient.full_name,
        total_prescriptions=len(summaries),
        prescriptions=summaries,
    )


async def get_pharmacy_queue(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
) -> PrescriptionListResponse:
    """
    Returns the pharmacy dispensing queue.

    Shows all prescriptions in 'issued' or 'dispensing' status,
    ordered oldest first (first in, first out).

    This is the primary view pharmacists use to work through
    their queue.

    Args:
        db:          Tenant database session
        hospital_id: From the JWT token
        page:        Page number
        per_page:    Results per page

    Returns:
        PrescriptionListResponse ordered oldest first
    """
    query = (
        select(Prescription)
        .where(
            Prescription.hospital_id == hospital_id,
            Prescription.status.in_([
                PrescriptionStatus.ISSUED,
                PrescriptionStatus.DISPENSING,
            ]),
        )
        .order_by(Prescription.created_at.asc())  # Oldest first (FIFO)
    )

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    prescriptions = result.scalars().all()

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    items = []
    for p in prescriptions:
        p = await _check_and_apply_expiry(db, p)
        items.append(await _build_summary(db, p, hospital_id))

    return PrescriptionListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )