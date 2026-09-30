"""
Alembic Migration Environment
-------------------------------
Configures Alembic to work with MedNexus's two-database architecture.

┌─────────────────────────────────────────────────────────────────┐
│                    ARCHITECTURE OVERVIEW                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Database 1: mednexus          (TENANT database)                 │
│    → Stores: hospital_users, patients, appointments,             │
│              prescriptions, lab, pharmacy, billing               │
│    → Base class: TenantBase                                      │
│    → Migrate with: MIGRATE_DB=tenant alembic ...                 │
│                                                                  │
│  Database 2: mednexus_platform (PLATFORM database)              │
│    → Stores: hospitals, plans, subscriptions, platform_admins    │
│    → Base class: PlatformBase                                    │
│    → Migrate with: MIGRATE_DB=platform alembic ...              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

CRITICAL — WHY THIS FILE HAS AN include_object FILTER:
────────────────────────────────────────────────────────
Both databases live on the same PostgreSQL server. When Alembic runs
autogenerate against the tenant database, it connects to the server
and sees ALL tables — including platform tables (hospitals, plans, etc.)
that physically exist on the same server in a different database.

Without the include_object filter, Alembic compares:
  - What it sees in the live database  (all tables on the server)
  - What it sees in TenantBase.metadata  (only tenant tables)

It concludes: "plans, hospitals, subscriptions, platform_admins exist
in the database but not in my metadata — they must have been deleted.
I will generate DROP TABLE statements for all of them."

This is catastrophically wrong. The include_object filter fixes this
by telling Alembic: "Only look at tables registered in the CURRENT
target metadata. Ignore everything else completely."

WHAT HAPPENS IF YOU REMOVE THE FILTER:
────────────────────────────────────────
Every autogenerate run will produce DROP TABLE statements for tables
belonging to the other database. Running those migrations will
permanently destroy live data. Do not remove this filter.

HOW TO ADD NEW MODELS:
────────────────────────
1. Create your model file in app/models/platform/ or app/models/tenant/
2. Make sure it inherits from PlatformBase or TenantBase respectively
3. Add its import to the correct section below (Platform models / Tenant models)
4. Run the correct migration command:
     Platform model: MIGRATE_DB=platform alembic revision --autogenerate -m "description"
     Tenant model:   MIGRATE_DB=tenant   alembic revision --autogenerate -m "description"
5. Review the generated file in migrations/versions/ before applying it
6. Apply with explicit revision IDs — see note below on multi-branch heads.

ALWAYS REVIEW GENERATED MIGRATION FILES BEFORE RUNNING THEM.
A clean migration should only contain create_table or alter_table
operations — never drop_table for tables you did not intentionally remove.

──────────────────────────────────────────────────────────────────
NOTE ON MULTI-BRANCH MIGRATION HEADS (added after Milestone 12):
──────────────────────────────────────────────────────────────────
This project's migrations/versions/ folder contains TWO independent
migration chains, each with its own root revision and branch_labels:

  Platform chain root: ae206b213a45 (branch_labels='platform')
  Tenant chain root:    64b90515a4c6 (branch_labels='tenant')

Because Alembic stores all revisions in one shared versions/ folder
regardless of MIGRATE_DB, commands like `alembic heads` and
`alembic upgrade head` operate on the ENTIRE graph, not just the
branch implied by MIGRATE_DB. This means:

  - `alembic heads` will always show TWO heads (one per chain)
  - The bare word `head` is AMBIGUOUS and must never be used alone
  - Always upgrade using the EXPLICIT revision ID:
      alembic upgrade <specific_revision_id>
  - When autogenerating against the platform DB, pin the target
    explicitly: --head platform@head
  - If autogenerate still complains "Target database is not up to
    date" due to the multi-head check, create the revision WITHOUT
    --autogenerate (plain `alembic revision -m "..." --head platform@head`)
    and hand-write the upgrade()/downgrade() body instead.

This is a permanent structural feature of this project, not a bug
to be fixed later. See Milestone 12 implementation notes for the
incident that led to this.

──────────────────────────────────────────────────────────────────
NOTE ON THE TENANT BRANCH (added at Milestone 16, revised at 17):
──────────────────────────────────────────────────────────────────
The "not up to date" friction documented above has, in practice,
mostly affected the PLATFORM branch (--head platform@head). The
TENANT branch has needed the same hand-written fallback on at least
one occasion (Milestone 16's generated_documents table) despite a
long clean streak before that. Treat BOTH branches as equally likely
to need the fallback — always check the autogenerate output before
assuming success, and fall back to a hand-written revision on the
FIRST failure rather than retrying autogenerate.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Path Setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import PlatformBase, TenantBase

# ── Model Imports ─────────────────────────────────────────────────────────────
# REQUIRED: Every model must be imported here so Alembic can detect it
# during autogenerate. A model that is not imported here is invisible
# to Alembic — it will not be included in migration generation.
#
# When you create a new model file:
#   1. Add its import in the correct section below
#   2. Use the correct MIGRATE_DB value when generating the migration
#
# The # noqa: F401 comment suppresses "imported but unused" linter warnings.
# These imports ARE used — they register the models with SQLAlchemy's
# metadata system as a side effect of being imported.

# ── Platform Models (mednexus_platform database) ──────────────────────────────
from app.models.platform import hospital            # noqa: F401
from app.models.platform import plan                # noqa: F401
from app.models.platform import platform_admin      # noqa: F401
from app.models.platform import subscription        # noqa: F401
from app.models.platform import subscription_event  # noqa: F401
from app.models.platform import support_ticket      # noqa: F401
from app.models.platform import announcement        # noqa: F401

# ── Tenant Models (mednexus database) ─────────────────────────────────────────
from app.models.tenant import appointment           # noqa: F401
from app.models.tenant import billing               # noqa: F401
from app.models.tenant import communication         # noqa: F401
from app.models.tenant import department            # noqa: F401
from app.models.tenant import generated_document    # noqa: F401
from app.models.tenant import hospital_user         # noqa: F401
from app.models.tenant import lab_order             # noqa: F401
from app.models.tenant import medical_record        # noqa: F401
from app.models.tenant import notification          # noqa: F401
from app.models.tenant import patient               # noqa: F401
from app.models.tenant import pharmacy              # noqa: F401
from app.models.tenant import prescription          # noqa: F401


# ── Alembic Config Object ─────────────────────────────────────────────────────
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ── Database Target Selection ─────────────────────────────────────────────────
db_target = os.environ.get("MIGRATE_DB", "tenant")

if db_target == "platform":
    target_metadata = PlatformBase.metadata
    db_url = settings.PLATFORM_DATABASE_URL
else:
    target_metadata = TenantBase.metadata
    db_url = settings.DATABASE_URL


# ── include_object Filter ─────────────────────────────────────────────────────
# THIS IS THE CRITICAL SAFETY FILTER. DO NOT REMOVE IT.
#
# Purpose: Restrict Alembic's autogenerate scope to only the tables
# that belong to the currently active metadata (tenant OR platform).
#
# How it works:
#   Alembic calls this function for every object it discovers during
#   schema comparison. If the function returns True, the object is
#   included in the comparison. If it returns False, it is ignored.
#
#   For table objects, we check whether the table name exists in
#   target_metadata.tables (the dict of tables registered with the
#   current Base class). Tables from the other database are not
#   registered here, so they return False and are ignored entirely.
#
#   Non-table objects (indexes, constraints, sequences) are always
#   included (return True) because they belong to their parent table
#   and will only appear if their parent table passed the filter.
#
# What this prevents:
#   Without this filter, running MIGRATE_DB=tenant autogenerate would
#   see platform tables in the database and generate DROP TABLE for them.
#   With this filter, those tables are invisible to the comparison.
#
# Parameters (provided by Alembic automatically):
#   obj:         The schema object being evaluated
#   name:        The name of the object (table name, column name, etc.)
#   type_:       The type: "table", "column", "index", "unique_constraint", etc.
#   reflected:   True if the object was found in the live database
#   compare_to:  The corresponding object in the metadata (None if not found)

def include_object(obj, name, type_, reflected, compare_to):
    """
    Scope filter that restricts autogenerate to the active metadata only.

    Returns True  → include this object in the migration comparison
    Returns False → ignore this object completely

    For tables: only include if registered in target_metadata.
    For everything else: always include (they belong to their parent table).
    """
    if type_ == "table":
        is_in_scope = name in target_metadata.tables
        if not is_in_scope:
            pass
        return is_in_scope
    return True


# ── Migration Runner: Offline Mode ────────────────────────────────────────────

def run_migrations_offline() -> None:
    """
    Generates migration SQL without a live database connection.
    Useful for previewing what a migration will do.
    """
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Migration Runner: Online Mode ─────────────────────────────────────────────

async def run_async_migrations() -> None:
    """Applies migrations to the live database asynchronously."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = db_url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations_with_connection)

    await connectable.dispose()


def _run_migrations_with_connection(conn) -> None:
    """
    Configures and runs migrations on an established database connection.
    Called by run_sync() to bridge async connection to sync Alembic internals.
    """
    context.configure(
        connection=conn,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Entry point for online migration mode."""
    asyncio.run(run_async_migrations())


# ── Execute ───────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()