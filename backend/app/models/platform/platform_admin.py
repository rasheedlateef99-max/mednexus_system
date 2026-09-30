"""
Platform Admin Model (Platform Database)
------------------------------------------
Represents LATLOMP's internal platform administrators.

IMPORTANT SECURITY NOTES:
- No public registration endpoint exists for this model
- Accounts are created manually by the platform owner
- Uses a completely separate JWT secret from hospital users
- Mandatory 2FA for all platform admins
- Passwords are hashed with bcrypt — never stored plain
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Enum as SAEnum, func, UUID, Text
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import PlatformBase


class PlatformAdminRole(str, enum.Enum):
    """
    Roles available to platform administrators.
    
    super_admin: Full access to everything, including other admin accounts
    admin:       Full operational access (approve hospitals, manage plans)
    support:     Read-only + support ticket management
    """
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    SUPPORT = "support"


class PlatformAdmin(PlatformBase):
    """
    Platform administrator table.
    Used exclusively by LATLOMP staff to manage the ecosystem.
    """
    __tablename__ = "platform_admins"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # bcrypt hash of the password — the plain password is never stored
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # ── Role & Permissions ────────────────────────────────────────────────────
    role: Mapped[PlatformAdminRole] = mapped_column(
        SAEnum(PlatformAdminRole),
        nullable=False,
        default=PlatformAdminRole.ADMIN,
    )

    # ── Account Status ────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Two-Factor Authentication ──────────────────────────────────────────────
    # All platform admins MUST have 2FA enabled
    totp_secret: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # Encrypted TOTP secret for authenticator apps
    two_factor_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
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
        return f"<PlatformAdmin id={self.id} email='{self.email}' role={self.role}>"