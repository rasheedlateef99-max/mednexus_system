"""
Seed Script: Create Initial Platform Admin
-------------------------------------------
Creates the first LATLOMP platform administrator account.

Run this script once to set up your first admin account.
Without this, there is no way to access the platform admin dashboard.

Usage:
    cd backend
    python scripts/seed_platform_admin.py

You will be prompted for:
  - Full name
  - Email address
  - Password (entered securely, not visible while typing)

The script will:
  1. Connect to the platform database
  2. Check if an admin with that email already exists
  3. Create the account with a hashed password
  4. Confirm success

After running this script, log in via:
  POST /api/v1/platform/auth/login
  
Then set up 2FA via:
  POST /api/v1/platform/auth/2fa/setup  (requires login token)
  POST /api/v1/platform/auth/2fa/confirm
"""

import asyncio
import sys
import os
import getpass

# Add the backend directory to Python path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import PlatformBase
from app.models.platform.platform_admin import PlatformAdmin, PlatformAdminRole
from app.core.security import hash_password


async def create_admin():
    """Interactive prompt to create the first platform admin account."""

    print("\n" + "=" * 60)
    print("  MedNexus — Platform Admin Account Setup")
    print("=" * 60)
    print("\nThis script creates your first platform administrator account.")
    print("This account will have SUPER ADMIN privileges.\n")

    # ── Collect Input ─────────────────────────────────────────────────────────
    full_name = input("Full name: ").strip()
    if not full_name:
        print("❌ Full name is required.")
        return

    email = input("Email address: ").strip().lower()
    if not email or "@" not in email:
        print("❌ A valid email address is required.")
        return

    # getpass hides the password while typing (does not echo to terminal)
    password = getpass.getpass("Password (min 8 characters): ")
    if len(password) < 8:
        print("❌ Password must be at least 8 characters.")
        return

    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("❌ Passwords do not match.")
        return

    # ── Connect to Platform Database ──────────────────────────────────────────
    print(f"\n⏳ Connecting to platform database...")

    engine = create_async_engine(settings.PLATFORM_DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        # Check if admin already exists
        result = await db.execute(
            select(PlatformAdmin).where(PlatformAdmin.email == email)
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"\n⚠️  An admin account with email '{email}' already exists.")
            print("   If you need to reset the password, use the password reset feature.")
            await engine.dispose()
            return

        # ── Create the Admin Account ──────────────────────────────────────────
        admin = PlatformAdmin(
            full_name=full_name,
            email=email,
            hashed_password=hash_password(password),
            role=PlatformAdminRole.SUPER_ADMIN,
            is_active=True,
            two_factor_enabled=False,  # They will set up 2FA after first login
        )

        db.add(admin)
        await db.commit()
        await db.refresh(admin)

        print("\n" + "=" * 60)
        print("  ✅ Platform admin account created successfully!")
        print("=" * 60)
        print(f"\n  Name:  {admin.full_name}")
        print(f"  Email: {admin.email}")
        print(f"  Role:  {admin.role.value}")
        print(f"  ID:    {admin.id}")
        print("\n  Next steps:")
        print("  1. Start your FastAPI server")
        print("  2. POST /api/v1/platform/auth/login with your credentials")
        print("  3. Use the returned token to POST /api/v1/platform/auth/2fa/setup")
        print("  4. Scan the QR code with Google Authenticator or Authy")
        print("  5. Confirm setup with POST /api/v1/platform/auth/2fa/confirm")
        print("\n" + "=" * 60 + "\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(create_admin())