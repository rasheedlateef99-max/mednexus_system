"""
Department Model (Tenant Database)
-------------------------------------
Represents an organisational department within a hospital.

Departments serve two purposes:
  1. Organisational: group staff by function (Cardiology, ICU, Pharmacy)
  2. Operational: scope appointments, lab orders, and records to a department

Every department belongs to exactly one hospital (via hospital_id).
Departments from Hospital A are completely invisible to Hospital B.

Examples:
  - Cardiology
  - Pediatrics
  - Emergency / A&E
  - Pharmacy
  - Laboratory
  - Radiology
  - Physiotherapy
  - Administration
  - Human Resources
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, func, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import TenantBase


class Department(TenantBase):
    """
    Department table in the tenant database.

    One row per department per hospital.
    The hospital_id column is the tenant isolation key.
    """
    __tablename__ = "departments"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Tenant Key ────────────────────────────────────────────────────────────
    # Every query against departments MUST filter by hospital_id.
    # This is what prevents Hospital A from seeing Hospital B's departments.
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # ── Department Information ────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Short code for internal reference (e.g. "CARDIO", "PHARM", "LAB")
    # Optional but useful for display in tight UI spaces
    code: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Head of department — references a HospitalUser ID
    # We store the UUID directly rather than a FK to avoid circular
    # dependency issues between departments and users at the model level.
    # The service layer validates that this user exists and belongs
    # to the same hospital.
    head_of_department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # ── Status ────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Department id={self.id} "
            f"name='{self.name}' "
            f"hospital_id={self.hospital_id}>"
        )