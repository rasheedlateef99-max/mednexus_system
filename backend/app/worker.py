"""
MedNexus Celery Worker
--------------------------
The Celery application instance. This is the entry point for both
the Celery worker process and the Celery beat scheduler.

Run worker:
    celery -A app.worker worker --loglevel=info

Run beat (scheduler):
    celery -A app.worker beat --loglevel=info

Run both together (development only — not recommended for production):
    celery -A app.worker worker --beat --loglevel=info

Design notes:
  - The Celery app is configured separately from the FastAPI app.
    It shares config values (Redis URL, database URLs) via app.config
    but does NOT import from app.main — this avoids circular imports
    and keeps the worker process lean.
  - Tasks use their own synchronous SQLAlchemy sessions (not async),
    because Celery workers run in a synchronous context by default.
    asyncio.run() can be used to bridge where needed — see task files.
  - Broker and result backend both use Redis. The result backend is
    needed only if you want to check task results programmatically;
    most sweep tasks are fire-and-forget and don't need it, but it's
    configured here for future tasks that might (e.g. report generation).

Worker Pool (platform-aware):
  Windows development uses the 'solo' pool because Windows does not
  support the Unix IPC primitives (shared memory semaphores, named
  pipes) that Celery's default 'prefork' pool relies on. Attempting
  to use prefork on Windows without administrator privileges causes
  PermissionError (WinError 5) and OSError (WinError 6) when the
  worker tries to dispatch tasks to subprocess workers.

  Linux (production) uses the default 'prefork' pool, which is the
  correct choice for multi-core task parallelism on Railway, Render,
  VPS, Docker, or Kubernetes. No deployment-time configuration change
  is needed — the pool is selected automatically by sys.platform.

Hosting-provider-agnostic:
  CELERY_BROKER_URL and CELERY_RESULT_BACKEND are read from environment
  variables. The worker command is always the same regardless of where
  it runs. Provider-specific configuration (number of workers, memory
  limits, restart policies) belongs in your hosting config, not here.
"""

import sys

from celery import Celery
from celery.schedules import crontab
from app.config import settings

# ── Celery App Instance ───────────────────────────────────────────────────────
celery_app = Celery(
    "mednexus",
    broker=settings.CELERY_BROKER_URL or settings.REDIS_URL,
    backend=settings.CELERY_RESULT_BACKEND or settings.REDIS_URL,
    include=[
        "app.tasks.subscription_tasks",
        "app.tasks.pharmacy_tasks",
    ],
)

# ── Celery Configuration ──────────────────────────────────────────────────────
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Worker pool
    # Windows: 'solo' avoids Unix IPC permission errors (WinError 5/6)
    # Linux:   'prefork' is the production default for multi-core parallelism
    # Selected automatically — no change needed at deployment time.
    worker_pool="solo" if sys.platform == "win32" else "prefork",

    # Task behavior
    task_acks_late=True,              # Acknowledge task AFTER completion, not before
                                       # Prevents task loss if a worker dies mid-execution
    worker_prefetch_multiplier=1,      # One task at a time per worker thread
                                       # Better for long-running tasks; prevents starvation
    task_reject_on_worker_lost=True,   # Re-queue if worker dies unexpectedly

    # Broker connection retry on startup (silences CPendingDeprecationWarning
    # in Celery 5.x; becomes the default behavior in Celery 6.0)
    broker_connection_retry_on_startup=True,

    # Result expiry — sweep tasks are fire-and-forget; results expire after 1 hour
    result_expires=3600,

    # Retry behavior for transient failures
    task_max_retries=3,
    task_default_retry_delay=60,       # 60 seconds between retries
)

# ── Beat Schedule (Periodic Tasks) ───────────────────────────────────────────
# crontab() expressions use UTC. All times here are UTC.
celery_app.conf.beat_schedule = {
    # Subscription expiry sweep — runs daily at 00:05 UTC
    # 5 minutes past midnight gives any timezone-adjacent midnight
    # processing a moment to complete before this sweep runs
    "sweep-expired-subscriptions": {
        "task": "app.tasks.subscription_tasks.sweep_expired_subscriptions",
        "schedule": crontab(hour=0, minute=5),
    },

    # Pharmacy expiry sweep — runs daily at 00:10 UTC
    "sweep-expired-pharmacy-batches": {
        "task": "app.tasks.pharmacy_tasks.sweep_expired_pharmacy_batches",
        "schedule": crontab(hour=0, minute=10),
    },

    # Low-stock check — runs every 6 hours
    # More frequent than expiry since stock can drop at any time of day
    "check-low-stock": {
        "task": "app.tasks.pharmacy_tasks.check_low_stock",
        "schedule": crontab(hour="*/6", minute=0),
    },
}

# ── Convenience alias ─────────────────────────────────────────────────────────
# Allows both `celery -A app.worker` and `celery -A app.worker.celery_app`
# to work. The former is cleaner in CLI usage.
app = celery_app