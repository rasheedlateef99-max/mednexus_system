"""
Subscription Background Tasks
-------------------------------------
Periodic sweeps for subscription lifecycle management.

sweep_expired_subscriptions:
  Runs daily at 00:05 UTC.
  Finds all subscriptions where:
    - status is TRIAL and trial_ends_at < now  → mark EXPIRED
    - status is ACTIVE and current_period_end < now  → mark PAST_DUE
    (PAST_DUE, not EXPIRED — a hospital that was active yesterday
     should get a grace period before being cut off. The platform admin
     can manually expire or a second sweep after N days can escalate.)

Why synchronous SQLAlchemy here:
  Celery workers run in a synchronous context. Using asyncio.run()
  inside a task is safe as long as we don't share event loops across
  tasks — which we don't, since each task call is isolated.
  Alternatively, celery-pool-asyncio can be used for a fully async
  worker setup, but it adds complexity not justified at this stage.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.worker import celery_app
from app.config import settings

logger = logging.getLogger(__name__)


async def _run_subscription_sweep() -> dict:
    """
    Async implementation of the subscription expiry sweep.
    Called by the Celery task via asyncio.run().
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select, and_
    from app.models.platform.subscription import Subscription, SubscriptionStatus

    engine = create_async_engine(settings.PLATFORM_DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    expired_trials = 0
    past_due_subscriptions = 0
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Expire trials past their end date
        trial_result = await session.execute(
            select(Subscription).where(
                and_(
                    Subscription.status == SubscriptionStatus.TRIAL,
                    Subscription.trial_ends_at < now,
                )
            )
        )
        for sub in trial_result.scalars().all():
            sub.status = SubscriptionStatus.EXPIRED
            expired_trials += 1
            logger.info("Expired trial subscription for hospital_id=%s", sub.hospital_id)

        # Mark active subscriptions past their period end as PAST_DUE
        active_result = await session.execute(
            select(Subscription).where(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.current_period_end < now,
                )
            )
        )
        for sub in active_result.scalars().all():
            sub.status = SubscriptionStatus.PAST_DUE
            past_due_subscriptions += 1
            logger.info("Marked subscription PAST_DUE for hospital_id=%s", sub.hospital_id)

        await session.commit()

    await engine.dispose()

    return {
        "expired_trials": expired_trials,
        "past_due_subscriptions": past_due_subscriptions,
        "swept_at": now.isoformat(),
    }


@celery_app.task(
    name="app.tasks.subscription_tasks.sweep_expired_subscriptions",
    bind=True,
    max_retries=3,
    default_retry_delay=300,  # 5 minutes between retries for this sweep
)
def sweep_expired_subscriptions(self):
    """
    Daily sweep: expire trials and mark overdue active subscriptions.
    Scheduled at 00:05 UTC by celery beat.
    Can also be triggered manually: sweep_expired_subscriptions.delay()
    """
    try:
        result = asyncio.run(_run_subscription_sweep())
        logger.info("Subscription sweep complete: %s", result)
        return result
    except Exception as exc:
        logger.error("Subscription sweep failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)