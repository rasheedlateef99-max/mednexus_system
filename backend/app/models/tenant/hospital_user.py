"""
Hospital User Model (Tenant Database)
---------------------------------------
Represents a staff member or administrator belonging to a hospital.

This model covers everyone who works at a hospital and uses the platform:
  - Hospital administrators (who registered the hospital)
  - Doctors
  - Nurses
  - Receptionists
  - Pharmacists
  - Lab scientists
  - Accountants

Every hospital user belongs to exactly one hospital (via hospital_id).
The hospital_id is the multi-tenancy key — it scopes ALL queries.

Authentication: Google OAuth 2.0 (primary)
              Email/Password (future fallback)
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    String, Boolean, DateTime, Enum as SAEnum,
    func, UUID, Text, ForeignKey
)
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import TenantBase


class HospitalUserRole(str, enum.Enum):
    """
    The role of a user within their hospital.

    hospital_admin:  Registered the hospital, full access to workspace
    doctor:          Clinical staff, access to patients/records/prescriptions
    nurse:           Clinical support, limited clinical access
    pharmacist:      Pharmacy module access
    lab_scientist:   Laboratory module access
    receptionist:    Front desk, appointments, patient registration
    accountant:      Billing and financial records access
    hr_manager:      Staff management access
    """
    HOSPITAL_ADMIN = "hospital_admin"
    DOCTOR = "doctor"
    NURSE = "nurse"
    PHARMACIST = "pharmacist"
    LAB_SCIENTIST = "lab_scientist"
    RECEPTIONIST = "receptionist"
    ACCOUNTANT = "accountant"
    HR_MANAGER = "hr_manager"


class HospitalUserStatus(str, enum.Enum):
    """
    The status of a hospital user account.

    active:      Can log in and use the platform
    inactive:    Account exists but access is disabled
    invited:     Invited but has not yet accepted / logged in
    """
    ACTIVE = "active"
    INACTIVE = "inactive"
    INVITED = "invited"


class HospitalUser(TenantBase):
    """
    Hospital user table (tenant database).

    One row per staff member per hospital.
    The hospital_id column is the tenant isolation key.
    """
    __tablename__ = "hospital_users"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Tenant Key ────────────────────────────────────────────────────────────
    # This is the most important column in the entire tenant database.
    # EVERY query against hospital data must filter by hospital_id.
    # This is what separates Hospital A's data from Hospital B's data.
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,  # Indexed because every single query filters on this
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Google OAuth ID — the unique identifier Google assigns to each account
    # This never changes even if the user changes their email
    google_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    # Profile picture URL from Google (optional, display only)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Password hash — for future email/password fallback
    # None when using Google OAuth only
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Role & Department ─────────────────────────────────────────────────────
    role: Mapped[HospitalUserRole] = mapped_column(
        SAEnum(HospitalUserRole),
        nullable=False,
        default=HospitalUserRole.HOSPITAL_ADMIN,
    )

    # Department ID — references the departments table (added in Milestone 4)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Professional details (for doctors, nurses, pharmacists)
    specialization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Status & Deactivation Audit ───────────────────────────────────────────
    status: Mapped[HospitalUserStatus] = mapped_column(
        SAEnum(HospitalUserStatus),
        nullable=False,
        default=HospitalUserStatus.ACTIVE,
        index=True,
    )

    # Whether this user is the original person who registered the hospital
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Who deactivated this user (references hospital_users.id of the admin
    # who performed the deactivation). Null for active users and for users
    # deactivated before this column was added (legacy rows).
    # Closes the audit gap documented in ADR-006.
    deactivated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # When this user's account was deactivated
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Login Tracking ────────────────────────────────────────────────────────
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

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
            f"<HospitalUser id={self.id} "
            f"email='{self.email}' "
            f"hospital_id={self.hospital_id} "
            f"role={self.role}>"
        )