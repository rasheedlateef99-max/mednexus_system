"""
Seed Script: Create Default Subscription Plans
------------------------------------------------
Creates the initial subscription plans for MedNexus.

Run once after database setup:
    cd backend
    python scripts/seed_plans.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models.platform.plan import Plan


PLANS = [
    {
        "name": "Free Trial",
        "code": "trial",
        "description": "7-day free trial with full access to core features",
        "price_monthly": 0,
        "price_annual": 0,
        "currency": "USD",
        "trial_days": 7,
        "has_trial": True,
        "max_staff_users": 10,
        "max_patient_records": 100,
        "max_appointments_per_month": 50,
        "features": {
            "appointments": True,
            "medical_records": True,
            "prescriptions": True,
            "lab_module": False,
            "pharmacy_module": False,
            "billing": False,
            "ai_assistant": False,
        },
        "is_active": True,
        "is_public": True,
        "display_order": 0,
    },
    {
        "name": "Starter",
        "code": "starter",
        "description": "Essential tools for small clinics and medical centers",
        "price_monthly": 99,
        "price_annual": 990,
        "currency": "USD",
        "trial_days": 7,
        "has_trial": True,
        "max_staff_users": 25,
        "max_patient_records": 5000,
        "max_appointments_per_month": 500,
        "features": {
            "appointments": True,
            "medical_records": True,
            "prescriptions": True,
            "lab_module": True,
            "pharmacy_module": False,
            "billing": True,
            "ai_assistant": False,
        },
        "is_active": True,
        "is_public": True,
        "display_order": 1,
    },
    {
        "name": "Professional",
        "code": "professional",
        "description": "Full-featured platform for hospitals and large medical centers",
        "price_monthly": 299,
        "price_annual": 2990,
        "currency": "USD",
        "trial_days": 7,
        "has_trial": True,
        "max_staff_users": 100,
        "max_patient_records": 50000,
        "max_appointments_per_month": 5000,
        "features": {
            "appointments": True,
            "medical_records": True,
            "prescriptions": True,
            "lab_module": True,
            "pharmacy_module": True,
            "billing": True,
            "ai_assistant": False,
        },
        "is_active": True,
        "is_public": True,
        "display_order": 2,
    },
    {
        "name": "Enterprise",
        "code": "enterprise",
        "description": "Unlimited access with AI features and dedicated support",
        "price_monthly": 999,
        "price_annual": 9990,
        "currency": "USD",
        "trial_days": 14,
        "has_trial": True,
        "max_staff_users": -1,
        "max_patient_records": -1,
        "max_appointments_per_month": -1,
        "features": {
            "appointments": True,
            "medical_records": True,
            "prescriptions": True,
            "lab_module": True,
            "pharmacy_module": True,
            "billing": True,
            "ai_assistant": True,
        },
        "is_active": True,
        "is_public": True,
        "display_order": 3,
    },
]


async def seed_plans():
    print("\n" + "=" * 60)
    print("  MedNexus — Seed Default Subscription Plans")
    print("=" * 60)

    engine = create_async_engine(settings.PLATFORM_DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        created = 0
        skipped = 0

        for plan_data in PLANS:
            result = await db.execute(
                select(Plan).where(Plan.code == plan_data["code"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                print(f"   ⏭️  Skipped: {plan_data['name']} (already exists)")
                skipped += 1
                continue

            plan = Plan(**plan_data)
            db.add(plan)
            created += 1
            print(f"   ✅ Created: {plan_data['name']}")

        await db.commit()

    print(f"\n   Plans created: {created}")
    print(f"   Plans skipped: {skipped}")
    print("\n" + "=" * 60 + "\n")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_plans())