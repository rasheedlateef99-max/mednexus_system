"""
Pharmacy Background Tasks
-------------------------------
Periodic sweeps for pharmacy inventory management.

sweep_expired_pharmacy_batches:
  Runs daily at 00:10 UTC.
  Finds all PharmacyStockBatch rows where expiry_date < today
  and quantity_remaining > 0. Writes quantity_remaining to 0 and
  recomputes PharmacyItem.total_quantity. These batches are kept
  (not deleted) for audit history — same pattern as the rest of the
  system where rows are never hard-deleted.

check_low_stock:
  Runs every 6 hours.
  Finds PharmacyItem rows where total_quantity <= reorder_level.
  Currently logs and returns the list — notification integration
  (creating a Notification row for the pharmacist) is a natural
  next step once a hospital-level "pharmacist user" can be identified
  without a manual lookup. Flagged here for future wiring.
"""

import asyncio
import logging
from datetime import date, datetime, timezone

from app.worker import celery_app
from app.config import settings

logger = logging.getLogger(__name__)


async def _run_pharmacy_expiry_sweep() -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select, and_, func
    from app.models.tenant.pharmacy import PharmacyItem, PharmacyStockBatch

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    batches_written_off = 0
    items_updated = set()
    today = date.today()

    async with async_session() as session:
        # Find all non-zero batches that have expired
        expired_result = await session.execute(
            select(PharmacyStockBatch).where(
                and_(
                    PharmacyStockBatch.expiry_date < today,
                    PharmacyStockBatch.quantity_remaining > 0,
                )
            )
        )
        expired_batches = expired_result.scalars().all()

        for batch in expired_batches:
            batch.quantity_remaining = 0
            items_updated.add(batch.pharmacy_item_id)
            batches_written_off += 1
            logger.info(
                "Wrote off expired batch %s (item_id=%s, expired=%s)",
                batch.id, batch.pharmacy_item_id, batch.expiry_date,
            )

        await session.commit()

        # Recompute total_quantity for each affected item
        for item_id in items_updated:
            qty_result = await session.execute(
                select(func.coalesce(func.sum(PharmacyStockBatch.quantity_remaining), 0))
                .where(
                    and_(
                        PharmacyStockBatch.pharmacy_item_id == item_id,
                        PharmacyStockBatch.expiry_date >= today,
                    )
                )
            )
            new_total = qty_result.scalar() or 0

            item_result = await session.execute(
                select(PharmacyItem).where(PharmacyItem.id == item_id)
            )
            item = item_result.scalar_one_or_none()
            if item:
                item.total_quantity = new_total

        await session.commit()

    await engine.dispose()

    return {
        "batches_written_off": batches_written_off,
        "items_recomputed": len(items_updated),
        "swept_at": datetime.now(timezone.utc).isoformat(),
    }


async def _run_low_stock_check() -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
    from app.models.tenant.pharmacy import PharmacyItem

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    low_stock_items = []

    async with async_session() as session:
        result = await session.execute(
            select(PharmacyItem).where(
                PharmacyItem.is_active == True,
                PharmacyItem.total_quantity <= PharmacyItem.reorder_level,
            )
        )
        items = result.scalars().all()

        for item in items:
            low_stock_items.append({
                "item_id": str(item.id),
                "hospital_id": str(item.hospital_id),
                "drug_name": item.drug_name,
                "total_quantity": item.total_quantity,
                "reorder_level": item.reorder_level,
                "reorder_quantity": item.reorder_quantity,
            })
            logger.warning(
                "Low stock: %s (hospital=%s) qty=%s reorder_at=%s",
                item.drug_name, item.hospital_id,
                item.total_quantity, item.reorder_level,
            )

    await engine.dispose()

    # Future: create Notification rows for pharmacist users here
    # See Milestone 20 notification_service.create_notification()

    return {
        "low_stock_count": len(low_stock_items),
        "items": low_stock_items,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@celery_app.task(
    name="app.tasks.pharmacy_tasks.sweep_expired_pharmacy_batches",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def sweep_expired_pharmacy_batches(self):
    """
    Daily sweep: write off expired pharmacy batches and recompute stock totals.
    Scheduled at 00:10 UTC by celery beat.
    Can also be triggered manually: sweep_expired_pharmacy_batches.delay()
    """
    try:
        result = asyncio.run(_run_pharmacy_expiry_sweep())
        logger.info("Pharmacy expiry sweep complete: %s", result)
        return result
    except Exception as exc:
        logger.error("Pharmacy expiry sweep failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.tasks.pharmacy_tasks.check_low_stock",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def check_low_stock(self):
    """
    Every-6-hours check: identify items below reorder level.
    Currently logs only. Future: trigger pharmacist notifications.
    Can also be triggered manually: check_low_stock.delay()
    """
    try:
        result = asyncio.run(_run_low_stock_check())
        logger.info("Low stock check complete: %s", result)
        return result
    except Exception as exc:
        logger.error("Low stock check failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)