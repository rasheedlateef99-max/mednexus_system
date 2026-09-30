# MedNexus — Architecture Decision Records

This document records all significant architectural decisions made during the
development of MedNexus. Each record includes the decision made, the reasoning
behind it, alternatives considered, and future roadmap notes.

This document is the authoritative engineering record. Do not rely on chat
history or informal notes as substitutes for entries here.

---

## ADR-001: Two-Database Architecture

**Date:** Project inception (Milestone 1)
**Status:** Permanent

### Decision
MedNexus operates two physically separate PostgreSQL databases:
- `mednexus` — tenant database (all hospital operational data)
- `mednexus_platform` — platform database (LATLOMP business data: hospitals, plans, subscriptions, admins)

### Reasoning
Hospitals (tenants) should never be able to access platform-owner data such as
subscription pricing, billing secrets, or other hospitals' records — even through
a misconfigured query or a compromised hospital admin account. Physical separation
at the database level enforces this boundary in a way that schema-level row
filtering cannot.

### Alternatives Considered
- Single database with schema-level separation (rejected: a query bug could still
  expose cross-tenant data; no hard boundary)
- Single database with row-level security (rejected: complexity of RLS policies
  at this stage; enforcement depends on application code correctness)

### Future Roadmap Notes
Cross-database joins are impossible in SQL; aggregations that span both databases
(such as the Audit Log in Milestone 18) must be done in Python after fetching
from each database separately. This is acceptable at current scale. If the
platform grows to thousands of hospitals and cross-database aggregations become
a performance bottleneck, evaluate read replicas or a dedicated analytics
database (e.g., a data warehouse fed by CDC) rather than collapsing the two
databases back into one.

---

## ADR-002: Two-Branch Alembic Migration Structure

**Date:** Milestone 12 (after migration contamination incident)
**Status:** Permanent

### Decision
All migration files live in one `migrations/versions/` folder, but the revision
graph contains two independent chains with explicit branch labels:
- `platform` branch root: `ae206b213a45`
- `tenant` branch root: `64b90515a4c6`

Every migration command must use an explicit revision ID or branch-qualified
target (`--head platform@head`, `--head tenant@head`). The bare word `head`
is permanently ambiguous and must never be used alone.

### Reasoning
A single linear chain (the original design) caused the platform database to
receive all tenant-side migrations during Milestone 12, because `alembic upgrade
head` had no way to distinguish which chain to walk. Separating the chains into
two independent branches with explicit labels isolates each database's migration
history, making cross-contamination structurally impossible.

### Alternatives Considered
- Two entirely separate Alembic environments with separate `alembic.ini` files
  (considered but rejected: more folder structure complexity for the same outcome)
- Single chain with careful ordering (the original approach, rejected after the
  contamination incident demonstrated it was not safe under normal usage patterns)

### Known Limitation
`alembic revision --autogenerate` against the platform branch consistently fails
with "Target database is not up to date" due to Alembic's multi-head pre-flight
check. The established fallback is: run `alembic revision -m "..." --head
platform@head` (without `--autogenerate`), inspect the generated empty file to
confirm `down_revision`, then hand-write the `upgrade()`/`downgrade()` bodies.
This fallback has also been needed on the tenant branch at least once (Milestone
16 onwards). Treat both branches as equally likely to need the hand-written path.

### Future Roadmap Notes
This is a permanent structural feature. Do not attempt to merge the two chains
back into one. If a third database is ever added, introduce a third branch root
following the same pattern.

---

## ADR-003: Cross-Database References Are Plain UUIDs (No Real FKs)

**Date:** Milestone 3 onward
**Status:** Permanent

### Decision
When a row in one database needs to reference a row in the other database
(e.g., `Invoice.appointment_id` in the tenant DB referencing an appointment,
or `TicketMessage.author_id` referencing either a `HospitalUser` or a
`PlatformAdmin`), the reference is stored as a plain `UUID` column with no
database-level foreign key constraint. Validation and resolution happen in the
service layer.

### Reasoning
PostgreSQL foreign key constraints cannot span two physically separate databases.
Attempting to enforce referential integrity at the DB level would require
collapsing the two-database architecture (which contradicts ADR-001) or
introducing a distributed transaction coordinator (which is out of scope at
this stage).

### Alternatives Considered
- Duplicating reference data across both databases (rejected: introduces sync
  complexity and data divergence risk)
- Soft references with periodic reconciliation jobs (rejected: unnecessary
  complexity when service-layer validation is sufficient)

### Future Roadmap Notes
If a specific cross-database reference becomes a common query target (e.g.,
looking up all invoices for a given hospital ID frequently), consider adding a
denormalized index column rather than changing the reference model.

---

## ADR-004: Free-Text Drug and Test Names (No Structured Catalogue)

**Date:** Milestone 8 (Prescriptions), confirmed at Milestone 19
**Status:** Current implementation — intentionally deferred, not rejected

### Decision
`PrescriptionItem.drug_name` and `LabOrderItem.test_name` are free-text
`VARCHAR` columns. There is no enforced relationship between prescribed drug
names and the `PharmacyItem` catalogue. Pharmacy stock deduction during
dispensing uses a case-insensitive string match (`find_pharmacy_item_by_drug_name`)
and proceeds with a warning note if no match is found rather than blocking.

### Reasoning
No real hospitals are using the platform yet. The actual failure mode of free
text (a dispensing warning instead of a hard stock deduction) is visible and
non-silent — the pharmacist sees "not in catalogue" in the dispensing notes and
can act manually. Building a structured FK relationship before real usage
demonstrates a concrete problem introduces complexity without a proven benefit
and risks constraining the workflow in ways that only become apparent once real
clinical staff are using the system.

### Alternatives Considered
**Option 2 (Frontend autocomplete against catalogue — deferred to frontend):**
`PrescriptionItem.drug_name` stays a string column. The web frontend
autocompletes against `GET /hospital/pharmacy/items` while the doctor types,
so names naturally align with stock without enforcing it at the backend. This
is the next natural evolution and is recorded on the frontend backlog.

**Option 3 (Nullable FK: `PrescriptionItem.pharmacy_item_id`):**
Doctor selects a structured catalogue entry, with a free-text fallback for
items not yet in stock. Guarantees exact dispensing linkage. Not rejected —
explicitly deferred until real hospital usage demonstrates the need. When
introduced, it must be backward-compatible: the `pharmacy_item_id` column
is nullable, so existing free-text prescriptions are not invalidated.

### Future Roadmap Notes
- **Frontend backlog:** Implement autocomplete against the pharmacy catalogue
  when building the prescription form (Option 2). This requires no backend
  changes — it is purely a frontend UX decision.
- **Enterprise evolution:** When the platform reaches sufficient scale that
  string-match dispensing errors are reported by actual users, introduce
  `PrescriptionItem.pharmacy_item_id` (nullable UUID, no FK constraint per
  ADR-003) and `LabOrderItem.lab_test_id` (nullable UUID referencing a future
  `LabTestCatalogue` table) in a backward-compatible additive migration.
  Existing rows remain valid. New prescriptions can optionally link to the
  catalogue while free text remains available for off-catalogue items.

---

## ADR-005: Pharmacy Stock Deduction Uses FEFO (First Expired, First Out)

**Date:** Milestone 10
**Status:** Permanent

### Decision
When pharmacy stock is deducted during prescription dispensing, batches are
consumed in expiry-date-ascending order (soonest-expiring first). This is
implemented in `deduct_stock_fefo()` in `pharmacy_service.py`.

### Reasoning
FEFO is standard pharmacy practice, mandated by most national pharmaceutical
regulatory bodies. Consuming oldest-expiring stock first minimizes waste from
expiry and reduces patient safety risk from dispensing near-expired medication.

### Alternatives Considered
- FIFO (first received, first out): standard in general inventory management
  but not appropriate for medications where expiry date and receipt date can
  diverge significantly
- Manual batch selection: too slow for high-volume pharmacy workflows

### Future Roadmap Notes
If the platform adds multi-location pharmacy (e.g., a hospital with multiple
dispensing counters each with their own physical stock), FEFO logic will need
to be scoped per location. The current implementation scopes correctly per
hospital but does not distinguish sub-locations.

---

## ADR-006: Audit Log Uses Derived Read-Only Aggregation (Option B)

**Date:** Milestone 18
**Status:** Current implementation — intentionally deferred to Option A, not permanent

### Decision
The platform-level audit log (`/api/v1/platform/audit-log/`) is implemented as
a read-only aggregation layer over existing actor/timestamp columns already
present on models (`approved_by`/`approved_at`, `cancelled_by`/`cancelled_at`,
etc.) across both databases. No new writes are introduced. The public contract
is a single function `list_audit_log()` returning `AuditLogListResponse` — the
router has no knowledge of the underlying sources.

### Reasoning
At the time of implementation, 15+ existing service files would have needed
modification to add explicit write-to-audit-log calls (Option A). Retrofitting
tested, stable code at the end of Phase 4 introduced more regression risk than
the audit completeness benefit justified. The derived approach exposes known
gaps honestly (`actor_id=None` where no actor column exists) rather than hiding
them.

### Known Gaps (rows with `actor_id=None`)
- Patient record updates: no `updated_by` column on `Patient`
- Staff deactivation: no `deactivated_by`/`deactivated_at` on `HospitalUser`
- Appointment cancellation: no `cancelled_by` on `Appointment`

### Alternatives Considered
**Option A (Dedicated `AuditLog` table, written to explicitly):** Every
sensitive action calls a logging function that inserts one structured row.
Gives complete, uniform coverage. Not rejected — explicitly planned as the
next evolution once the platform has real users generating real audit data
that needs forensic-level completeness.

### Future Migration Path to Option A
The current design is architected for a clean swap:
1. Add a `AuditLog` model and migration (new table, no changes to existing tables)
2. New actions start writing to it going forward
3. Replace only the body of `list_audit_log()` — the function signature and
   `AuditLogListResponse` return type stay identical, so the router and all
   callers need zero changes
4. Optionally, during a transition period, `list_audit_log()` can query the
   new table for events after its creation date and fall back to the current
   `_collect_*` functions for historical data — preserving full history without
   a backfill migration

### Future Roadmap Notes
When introducing the dedicated `AuditLog` table (Phase 5B hardening or later):
- Add `updated_by` columns to `Patient`, `HospitalUser`, `Appointment` at the
  same time (small additive migrations) to close the known gaps
- The `_collect_*` functions in `audit_log_service.py` serve as a documented
  map of every source that was ever used — consult them when designing the
  new table's `action` enum values to ensure backward-compatible history

---

## ADR-007: Paystack as Primary Payment Provider (Abstracted Behind Interface)

**Date:** Milestone 12
**Status:** Current — additional providers deferred

### Decision
Paystack is used as the payment provider for hospital subscription billing.
All Paystack-specific logic is isolated in `PaystackProvider`, which implements
the abstract `PaymentProvider` interface. `subscription_service.py` depends
only on the interface, never on Paystack directly. Webhook security uses
HMAC-SHA512 signature verification against the raw request body.

### Reasoning
Paystack is strong for the primary target market (Nigeria/West Africa: NGN, GHS,
KES). Abstracting behind `PaymentProvider` ensures adding Stripe, Flutterwave,
or a regional provider later requires writing one new class, not modifying the
subscription logic.

### Future Roadmap Notes
- Switching providers: implement `StripeProvider(PaymentProvider)` or similar,
  then update `get_payment_provider()` in `paystack_provider.py` — one function
  change, everything else is untouched
- Separate Paystack account: before processing real hospital payments, open a
  dedicated Paystack merchant account under MedNexus's own business registration
  rather than reusing a personal account. Using a shared account risks
  settlement mixing and compliance issues if either account is reviewed.

---

## ADR-008: PDF Generation Uses WeasyPrint + Jinja2 HTML Templates

**Date:** Milestone 16
**Status:** Current — vendor dependency noted

### Decision
PDF documents (invoices, prescriptions, lab reports) are rendered via WeasyPrint,
which converts Jinja2 HTML templates to PDF bytes. Generated PDFs are stored in
Cloudflare R2 (S3-compatible). Download URLs are time-limited presigned URLs
(default 1 hour). The R2 bucket is never public.

### Reasoning
HTML/CSS templates are dramatically easier to maintain, review, and hand off to
a designer than programmatic PDF drawing APIs (ReportLab canvas approach).
Cloudflare R2 has zero egress fees, which matters once hospitals regularly
download patient documents.

### Windows-Specific Note
WeasyPrint requires GTK system libraries (Pango, Cairo, GObject) not included
in the Python package on Windows. On Windows development machines, install the
GTK runtime from:
`https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases`
Select the 64-bit installer and check "Add GTK to system PATH" during setup.
Close and reopen all terminals after installation.

### Future Roadmap Notes
- Template customization: hospitals should eventually be able to upload a logo
  and set brand colors — this is a CSS/template variable change, not a code
  change, which was a deliberate goal of the HTML template approach
- If WeasyPrint performance becomes a bottleneck at high PDF volume, move
  generation to a background job (Milestone 22 scope) rather than generating
  synchronously in the request handler

---

## ADR-009: Internal Communications Use Conversation/Participant/Message Pattern

**Date:** Milestone 17
**Status:** Permanent

### Decision
Staff-to-staff messaging uses three tables: `Conversation`, `ConversationParticipant`,
`Message`. Read tracking is a single `last_read_at` cursor per participant
(not a per-message read-receipt row). Direct conversation deduplication prevents
two staff members from accumulating multiple separate DM threads.

### Reasoning
The three-table pattern handles 1:1 DMs and group/department conversations
with one schema, eliminating the need for two separate message systems.
A `last_read_at` cursor scales linearly with conversation count, not message
count — the standard approach used by Slack, WhatsApp, and every production
messaging system for this reason.

### Future Roadmap Notes
- File attachments in messages: add `message_attachments` table referencing R2
  storage keys — additive, no changes to existing schema
- Real-time delivery (WebSocket): the current REST polling model is sufficient
  during testing but should be upgraded to WebSocket push (FastAPI supports this
  natively) when the frontend is built, so the inbox feels live rather than
  requiring manual refresh
- Message search: a `tsvector` full-text search index on `Message.body` can be
  added as a migration with no application code changes — defer until there is
  real message volume to justify it

---

## Deferred Items Register

The following items have been explicitly deferred and must not be forgotten.
Each entry references the ADR where the deferral was decided.

| Item | Deferred In | When to Revisit |
|------|-------------|-----------------|
| Frontend catalogue autocomplete for drug/test names | ADR-004 | Web frontend build (Phase 6) |
| Nullable `pharmacy_item_id` FK on `PrescriptionItem` | ADR-004 | When real dispensing mismatches are reported by actual users |
| Dedicated `AuditLog` table (Option A) | ADR-006 | Phase 5B hardening, or when forensic audit completeness becomes a compliance requirement |
| `updated_by` columns on Patient, HospitalUser, Appointment | ADR-006 | Same time as AuditLog table introduction |
| Stripe/Flutterwave payment provider | ADR-007 | When entering markets where Paystack has poor coverage |
| Dedicated Paystack merchant account for MedNexus | ADR-007 | Before processing first real hospital payment |
| PDF generation as background job | ADR-008 | Milestone 22 (background jobs) or when PDF generation latency becomes user-visible |
| WeasyPrint HTML template customization per hospital | ADR-008 | Phase 6 (web frontend) — hospital branding feature |
| WebSocket real-time messaging delivery | ADR-009 | Phase 6 (web frontend build) |
| Message search (full-text index on Message.body) | ADR-009 | When real message volume justifies it |
| Multi-location pharmacy FEFO scoping | ADR-005 | When a hospital with multiple dispensing locations onboards |