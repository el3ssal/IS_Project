# Architecture Review — Wedding Hall Management System

**Project:** Wedding Hall Management System, Introduction to IS course project (solo, Ahmed Ashraf Elassal)
**Phase:** 0 — Requirements analysis (fresh restart, solo)
**Date:** 2026-11-26
**Sources:** instructor deck (slides 2–4, 13, 21, 29, 135) and SRS v1.0 §0.1, §16–§18, §25.

## 1. Review of the proposed 3-tier architecture

### 1.1 What the deck requires

- Slide 2, Architecture row: "UI, logic and data tiers in separate files".
- Slide 3, steps 3–5: step 3 builds the data tier (`database.py`, CRUD and queries), step 4 the logic tier (models, service, rules → "Validated classes"), step 5 the UI tier (tabs, forms, tables → "Working screens").
- Slide 4 and the sequence diagrams (slides 13, 21, 29, …) show the same four swimlanes in every workflow: User → UI (Gradio) → Service → Database, with the service raising `ValidationError` and the UI converting it to `gr.Error`.
- The activity diagrams (slides 12, 20, 28, …) put validation and rule checks in the logic tier and the `INSERT`/`commit` in the data tier.

### 1.2 What the SRS proposes

SRS §17 proposes a `wedding_hall_system/` package with `main.py`, `config/`, `ui/` (12 view modules + `components/`), `logic/` (models, exceptions, permissions, validators, 16 services), `data/` (`database.py`, `schema.sql`, `migrations/`, `repositories/`), `storage/` (uploads, backups), `reports/`, `tests/` and `docs/`. SRS §0.1 fixes the tier contract: presentation = Gradio screens and event handlers; business logic = services, policies, validation, calculations, workflows; data access = SQLite connection management, repositories/queries, schema and migrations.

### 1.3 What was checked and found adequate

- **Tier separation is real, not nominal.** Every workflow in SRS §19 (UC-01–UC-11) maps to UI → service → repository; no use case requires SQL in a view or a business rule in a handler. The deck's own diagrams enforce the same shape.
- **The service list covers the use cases.** Each UC has an owning service: customers → `customer_service.py`, quotations → `quotation_service.py`, bookings → `booking_service.py` + `availability_service.py`, contracts → `contract_service.py`, tasks → `task_service.py` + `staff_service.py`, payments/expenses/refunds → `payment_service.py` / `expense_service.py` / `refund_service.py`, reports → `report_service.py`, files → `attachment_service.py`, backup → `backup_service.py`, cross-cutting → `audit_service.py` / `notification_service.py` / `pricing_service.py`.
- **The exception hierarchy is sufficient.** SRS §17.2 lists 12 exceptions (`ApplicationError` → `ValidationError`, `AuthenticationError`, `AuthorizationError`, `NotFoundError`, `BookingConflictError`, `HallUnavailableError`, `InsufficientDepositError`, `PaymentError`, `RefundLimitExceededError`, `DatabaseOperationError`, `AttachmentValidationError`). Each maps to a user-facing message in SRS §22.
- **The model list is sufficient.** SRS §17.1 names 16 domain classes; the deck needs only 3. No class is gratuitous: each owns validation or behaviour, not just data.

### 1.4 Findings from the review (AR-01–AR-14)

| ID | Finding | Decision |
|---|---|---|
| AR-01 | SRS §17 puts `exceptions.py`, `permissions.py`, `validators.py` under `logic/`. Permissions and validation are cross-tier utilities, not business rules. | Keep them in `logic/` as the SRS proposes (they are imported by services, never by `ui/` directly), but keep them free of Gradio and SQL so the boundary stays clean. |
| AR-02 | The SRS structure has no `utils/` package; logging config and validators would otherwise float at the package root. | Add `utils/` for `logging_config.py` (redacting filter) and shared helpers. `validators.py` stays in `logic/` per SRS §17. |
| AR-03 | `data/database.py` (manager) and `data/repositories/` overlap in responsibility if both expose query helpers. | One rule: `DatabaseManager` owns connections, pragmas, transactions and backup; repositories own SQL text per aggregate and call the manager. Services never open connections. |
| AR-04 | The SRS structure lists no `conftest.py` location for fixtures; tests need deterministic seed data. | `tests/conftest.py` builds a temp SQLite file, runs `schema.sql` + seed, and tears it down. No test ever touches the real database. |
| AR-05 | Auth/permissions are queried by almost every service; duplicating permission SQL in each service would rot. | One `logic/permissions.py` (`get_employee_permissions`, `has_permission`, `require_permission`) backed by the `roles`/`permissions`/`role_permissions` tables. Services call it; they do not write their own permission SQL. |
| AR-06 | The SRS lists two snapshot tables for policies: `booking_policy_snapshots` (§18.5) and `financial_policy_snapshots` (§18.8). They look mergeable, but they snapshot different things (booking/hold conditions vs deposit/installment/refund policy). | Keep both tables, as the SRS lists them. Merging them would hide which policy a refund or cancellation was computed under. |
| AR-07 | SRS §18.4 allows `discount_records` *or* discount fields on the booking snapshot. | Keep `discount_records` as its own table: a discount has an employee, a reason, a limit check and an audit trail, which a flat snapshot field cannot carry. |
| AR-08 | SRS §18.3 allows `hall_facilities` as a table *or* JSON/text. | Store facilities as a JSON array in `halls.facilities_json`. Facilities are descriptive attributes (SRS §4.1), never queried by value, so a join table adds cost without benefit. |
| AR-09 | SRS §18.5 allows `booking_notes_history` *or* the audit log. | No separate table: notes edits are audit-log events (`entity_type='booking'`, action `booking.notes_updated`, old/new JSON). One history mechanism, fewer tables. |
| AR-10 | `staff_assignment_conflicts` is marked optional / audit-only (SRS §18.7). | No table. Conflicts are recorded as audit events + notifications (SRS §7.4 requires the Manager's decision in audit history, not a dedicated table). |
| AR-11 | SRS §18.5 lists `booking_transfers` "(if needed)" and §8.11–§8.12 need a transfer record. | Keep `booking_transfers`: ownership transfer must preserve old/new owner, reason and approver, which the audit log alone cannot query efficiently. |
| AR-12 | `service_halls` is listed "where needed" (§18.4). Services are venue-wide by default; hall suitability is a filter, not a booking constraint. | Keep the table: it records which services a hall can host, used by the "compatible services" filter (SRS §3.6). |
| AR-13 | The SRS structure has no place for the audit trail of *settings* changes (SRS §14 says config changes must not rewrite history, and §12.1 lists role/permission changes as audited). | `audit_logs` covers it (`entity_type='system_setting'` / `'role'`); `system_settings` stores current values only. |
| AR-14 | Nothing in the SRS structure enforces the "no giant `main.py`" rule (SRS §16.5) structurally. | `main.py` is a thin launcher (~30 lines): parse env, build settings, initialize DB, launch Gradio. All wiring lives in `ui/app.py` and `config/`. |

## 2. Final proposed project structure

Solo project, built from scratch (the GitHub repo `el3ssal/IS_Project` was deleted by Ahmed on 2026-11-26 and will be recreated fresh when there is code to push; Phase 0 writes documentation only, locally under `files/is-project/`). No team scaffold is preserved (this restart is solo). The structure follows SRS §17 with the AR-01/AR-02 adjustments above.

```text
IS_Project/
├── main.py                     # Thin launcher only (~30 lines)
├── requirements.txt            # gradio only (stdlib otherwise); each line justified
├── README.md                   # Overview, stack, setup, demo accounts, tests, LAN, limitations
├── .gitignore                  # *.db, .venv/, storage/uploads/*, storage/backups/* (keep .gitkeep)
├── config/
│   ├── __init__.py
│   ├── settings.py             # AppSettings: venue, currency, timezone, hold/grace/buffer,
│   │                           # deposit %, session length, lockout, PBKDF2 iterations
│   └── defaults.py             # Seed roles, permissions, role→permission map, settings, demo users
├── ui/
│   ├── __init__.py
│   ├── app.py                  # build_app(): Gradio Blocks, login view, main view, routing
│   ├── auth_views.py           # Login, change password, logout
│   ├── dashboard_views.py      # Role dashboards + Pending bookings dashboard
│   ├── customer_views.py
│   ├── hall_views.py
│   ├── booking_views.py
│   ├── quotation_views.py
│   ├── contract_views.py
│   ├── operations_views.py     # Event plans, tasks, staff
│   ├── finance_views.py        # Payments, expenses, refunds, installments
│   ├── reports_views.py        # Reports, search, CSV export
│   ├── settings_views.py       # Owner configuration
│   └── components/
│       └── __init__.py         # Shared Gradio components (tables, status chips)
├── logic/
│   ├── __init__.py
│   ├── models.py               # Dataclasses: Customer, Hall, Booking, Quotation, Package,
│   │                           # Service, Employee, Task, Payment, Expense, Refund, Contract
│   ├── exceptions.py           # ApplicationError hierarchy (SRS §17.2, 12 types)
│   ├── permissions.py          # get_employee_permissions / has_permission / require_permission
│   ├── validators.py           # Text, phone (Egyptian), email, amounts, datetimes
│   ├── auth_service.py         # Login, sessions, lockout, change/reset password
│   ├── pricing_service.py      # Price breakdown, discounts, snapshots
│   ├── availability_service.py # Overlap/buffer/hours/maintenance/closure checks (half-open)
│   ├── booking_service.py      # Lifecycle, holds, confirm, cancel, reschedule, no-show, transfer
│   ├── quotation_service.py    # Create/accept/convert quotations
│   ├── customer_service.py     # CRUD, duplicate detection, interactions
│   ├── contract_service.py     # Templates, versions, PDF approach
│   ├── staff_service.py        # Profiles, availability, time off
│   ├── task_service.py         # Templates, event plans, assignment warnings
│   ├── payment_service.py      # Record, reversal/void, installments, overpayments
│   ├── expense_service.py      # Record, approve/reject, payment status
│   ├── refund_service.py       # Workflow, cap enforcement
│   ├── report_service.py       # Dashboards, reports, CSV export, charts
│   ├── notification_service.py # In-app notifications, role routing
│   ├── audit_service.py        # Audit writes (same-transaction where possible)
│   ├── attachment_service.py   # Upload/download/preview authorization, validation
│   └── backup_service.py       # Backup/restore orchestration
├── data/
│   ├── __init__.py
│   ├── database.py             # DatabaseManager: connections, pragmas, transactions, backup
│   ├── schema.sql              # Full schema v1 (see §3)
│   ├── seed.py                 # Idempotent seed: roles, permissions, settings, demo users
│   ├── migrations/
│   │   └── 001_initial.sql     # Copy of schema.sql v1 (migrations are versioned files)
│   └── repositories/
│       ├── __init__.py
│       ├── employee_repository.py
│       ├── customer_repository.py
│       ├── hall_repository.py
│       ├── booking_repository.py
│       ├── finance_repository.py
│       └── system_repository.py  # settings, audit, notifications
├── utils/
│   ├── __init__.py
│   └── logging_config.py       # Logger + redacting filter (no secrets in logs)
├── storage/
│   ├── uploads/                # Attachment bytes (never served statically; .gitkeep only)
│   └── backups/                # SQLite backups + metadata (.gitkeep only)
├── reports/                    # Generated CSV exports (git-ignored)
├── tests/
│   ├── conftest.py             # Temp DB fixture, seed, login helpers
│   ├── test_console.py         # Deck's 3 console cases incl. bad input (AC-008)
│   ├── test_auth.py
│   ├── test_permissions.py
│   ├── test_database.py
│   ├── test_customers.py
│   ├── test_availability.py
│   ├── test_bookings.py
│   ├── test_pricing.py
│   ├── test_payments.py
│   ├── test_refunds.py
│   └── test_reports.py
└── docs/
    ├── REQUIREMENT_TRACEABILITY_MATRIX.md
    ├── GAP_ANALYSIS.md
    ├── ARCHITECTURE_REVIEW.md
    ├── SRS.md                  # Copy of the SRS
    ├── ASSUMPTIONS_AND_DECISIONS.md
    ├── USE_CASES.md            # UC-01–UC-11 (SRS §19)
    ├── DEMO_SCRIPT.md          # 2-minute walkthrough
    ├── UML/                    # Use Case, Class, Activity, Sequence diagrams
    └── DFD/                    # Context, Level 0, Level 1 diagrams
```

**Enforcement rules for the structure**

1. `ui/` imports `logic/` only. It never imports `data/` and never contains SQL or business rules.
2. `logic/` imports `data/` (repositories, manager) and `config/` only. It never imports Gradio.
3. `data/` imports `config/` only. It contains no business rules and no Gradio.
4. Every service method that changes state checks permissions first (`require_permission`) and writes its audit row in the same transaction as the change (NFR-016).
5. Money is stored as integer piasters (EGP × 100) in every amount column; formatting happens only at display. This avoids float rounding in financial totals.
6. All timestamps are stored as ISO-8601 text in the venue timezone (Africa/Cairo, from settings); no naive datetimes cross a tier boundary.

## 3. Database design

### 3.1 Design principles (validated against SRS §18.10)

- Every table has a surrogate integer primary key (`id`), except pure link tables, which use composite keys.
- `PRAGMA foreign_keys = ON` on every connection; `busy_timeout` set; WAL mode considered only after validating the deployment filesystem (SRS §16.2).
- `UNIQUE` on username, contract number, booking number, quotation number.
- `CHECK` constraints: `end_at > start_at`, guest count > 0, non-negative monetary amounts (signed entries only where a documented ledger design requires them), refund totals enforced in the service layer (SQLite cannot express a cross-table sum as a CHECK).
- Soft deactivation (`is_active` / `status`) for halls, packages, services, event types and employees; history is never deleted (BR-23).
- Explicit transaction boundaries for every financial and booking workflow.
- **Critical (SRS §18.10):** SQLite has no exclusion constraint for overlapping ranges. Overlap is enforced in `availability_service.py` inside a `BEGIN IMMEDIATE` transaction, with a re-check immediately before commit.

### 3.2 Entities and relationships (final table list)

SRS §18 names 56 entities. After the documented merges in §1.4 (AR-08: facilities as JSON; AR-09: notes history via audit log; AR-10: staff conflicts audit-only) and keeping both policy-snapshot tables (AR-06), the final schema has **53 tables**. Tables marked ◇ are conditional/optional in the SRS and kept for the reason given.

**Identity and authorization (7)** — `employees`, `roles`, `permissions`, `role_permissions`, `employee_roles`, `login_attempts`, `sessions`.
`employees` is both the login account and the staff profile (GAP G-20): username (UNIQUE), password hash + salt, full name, contact, job role/specialties, availability link, active flag. `employee_roles` is many-to-many; one employee normally holds one role, but the link table keeps the SRS's multi-role option open.

**Customers and interactions (3)** — `customers`, `customer_contacts`, `customer_interactions`.
`customers`: full name, primary phone (normalized), email (normalized, lowercase), address, National ID (restricted), internal notes, timestamps. `customer_contacts`: additional phones. `customer_interactions`: calls, inquiries, quotations, booking changes, cancellations, payments, contracts, notes.

**Halls and schedules (4 + JSON)** — `halls`, `hall_weekly_hours`, `hall_special_hours`, `maintenance_blocks`.
`halls`: name, description, capacity, base price, status (Active/Inactive/Under Maintenance), `facilities_json`, suitable packages/services (via link tables), notes. `hall_weekly_hours`: hall, day of week, intervals (multiple per day, closed days = no rows). `hall_special_hours`: exceptional dates (holiday/seasonal closure or opening). `maintenance_blocks`: hall, type, description, start/end, recording employee, created timestamp.

**Catalog and pricing (11)** — `event_types`, `packages`, `package_halls`, `package_event_types`, `services`, `package_services`, `service_halls` ◇, `pricing_rules`, `discount_records`, `task_templates`, `task_template_items`.
`packages`: name, description, base price (piasters), default/minimum duration, included guest count, active flag. `services`: name, pricing method (per event / per hour / per guest / by level), rate, active flag. `pricing_rules`: hall/guest-count/weekday/season/duration/package/service conditions + adjustment. `discount_records`: booking, employee, amount or %, reason, timestamp.

**Quotations and bookings (9)** — `quotations`, `quotation_items`, `bookings`, `booking_price_items`, `booking_services`, `booking_history`, `booking_policy_snapshots`, `booking_transfers` ◇, `reschedule_history`.
`bookings`: booking number (UNIQUE), customer, hall (exactly one — BR-01), event type, package, `start_at`, `end_at`, guest count, status, notes, additional contact, hold expiry, timestamps. `booking_price_items`: the frozen price snapshot (component, amount). `booking_history`: every status/field transition with actor + old/new values. `reschedule_history`: old/new date/time/hall + price delta + approval.

**Contracts and files (3)** — `contracts`, `contract_versions`, `attachments`.
`contracts`: contract number (UNIQUE), booking, template, signature status, current version pointer. `contract_versions`: version number, content snapshot, created by/at, supersedes. `attachments`: entity type + ID, original filename, generated storage name/path, MIME/type, size, category, uploader, upload timestamp, soft-delete flag.

**Operations (4)** — `employee_availability`, `event_plans`, `tasks`, `task_history`.
`event_plans`: one per confirmed booking. `tasks`: plan, template item, name, description, assigned employee, priority, due date/time, status, service link, timestamps. `task_history`: status/assignment changes. (`staff_assignment_conflicts`: no table — audit-only, AR-10.)

**Finance (8)** — `payments`, `payment_corrections`, `installments`, `expenses`, `refunds`, `financial_policy_snapshots`, `cancellation_records`, `no_show_records`.
`payments`: booking, amount (piasters), date/time, method, reference, recording employee, status, initiation timestamp, notes. `payment_corrections`: original payment, reversal/void type, reason, employee, timestamp. `installments`: booking, due date, amount, status. `expenses`: amount, date/time, category, description, method, employee, scope (venue/hall/event), approval status, payment status, attachment link. `refunds`: original payment, booking, amount, reason, method, employee, timestamp, status. `cancellation_records` / `no_show_records`: fee, refundable amount, policy snapshot reference, actor, timestamp.

**System (4)** — `notifications`, `audit_logs`, `system_settings`, `backup_records` ◇.
`notifications`: recipient role/employee, type, priority, linked entity, read flag, timestamp. `audit_logs`: employee, action, entity type + ID, timestamp, old/new JSON, description/reason. `system_settings`: key/value (venue, currency, timezone, hold/grace/buffer, deposit %, discount limits, notification config, backup location). `backup_records`: timestamp, path, checksum, result, actor.

### 3.3 Indexes (required for correctness and performance)

Availability is the hot path (every quotation, booking, reschedule and search hits it), so these indexes are required, not optional:

- `bookings (hall_id, start_at, end_at, status)` — overlap queries.
- `bookings (customer_id, start_at)` — customer history.
- `bookings (status, start_at)` — Pending dashboard, hold expiry sweep.
- `maintenance_blocks (hall_id, start_at, end_at)` — maintenance overlap.
- `hall_special_hours (hall_id, date)` — exceptional dates.
- `hall_weekly_hours (hall_id, day_of_week)` — opening hours.
- `payments (booking_id, created_at)` — paid/outstanding totals.
- `refunds (payment_id, status)` — refund-cap enforcement.
- `audit_logs (entity_type, entity_id, created_at)` — booking timeline.
- `audit_logs (employee_id, created_at)` — audit search.
- `tasks (assigned_employee_id, status, due_at)` — staff dashboard.
- `notifications (recipient_role, is_read, created_at)` — inbox queries.
- `customers (phone_normalized)`, `customers (email_normalized)` — duplicate detection.
- `attachments (entity_type, entity_id)` — file lists per record.

### 3.4 Transactions — the rules that prevent double booking

1. **Booking creation / quotation conversion / reschedule** — one `BEGIN IMMEDIATE` transaction: (a) expire overdue holds; (b) load hall status, hours, special hours, maintenance; (c) run the overlap query over confirmed + active Pending bookings with buffer applied, half-open `[start, end)`; (d) re-check availability immediately before commit; (e) insert booking + price snapshot + history + audit rows; (f) commit. Any failure rolls back everything (NFR-011).
2. **Payment recording + booking confirmation** — one transaction: insert payment, recompute paid total, check deposit threshold + hold/grace validity, set Confirmed + snapshot policies + generate event plan, write audit + notification, commit. A payment that confirms a booking never commits without its confirmation (SRS §8.2 idempotency: a unique idempotency key per recording request prevents double-submit duplicates).
3. **Refund approval/payment** — one transaction: re-sum completed/approved refunds for the payment, enforce the cap (BR-15), update refund status, write audit, commit.
4. **Cancellation** — one transaction: compute fee/refund from the confirmation-time snapshot, set Cancelled, release the hall, create the refund request if due, write audit, commit. Refund *payment* is a separate later transaction (BR-16).
5. **Audit coupling** — the audit row for a critical operation is written in the same transaction as the operation (NFR-016). If the audit write fails, the operation rolls back.

### 3.5 Missing entities / constraints found in review

- **No idempotency key on payments in SRS §18.** Added: `payments.idempotency_key` (UNIQUE, nullable) so a retried/double-clicked recording cannot create two payments (SRS §8.2 requires this behaviour; the column is the mechanism).
- **No hold-expiry bookkeeping in SRS §18.** Added: `bookings.hold_expires_at` + `bookings.grace_expires_at` columns so the lazy-expiry sweep (GAP G-11) and the Pending dashboard countdown have stored values to read.
- **No link from refund to its approval.** `refunds` carries `approved_by` / `approved_at` columns (SRS §8.7 workflow needs an approver, but §18 does not list the columns).
- **No contract-template table in SRS §18.** Templates are rows in `system_settings` (template name → content) plus versioning through `contract_versions`; a separate table is unnecessary for a solo project and the SRS does not name one.
- **No `schema_version` table in SRS §18.** Added: `schema_version (version INTEGER PRIMARY KEY, applied_at)` — migrations are versioned files (SRS §16.5), so the DB must record which version it is on.

### 3.6 What the design deliberately does NOT include

- No customer portal tables (out of scope, SRS §1.4).
- No inventory/supplier/payroll/tax tables (out of scope).
- No waiting-list table (SRS §9.4: no waiting-list module).
- No promo-code/loyalty tables (SRS §3.7: none).
- No equipment tables (SRS §1.4: equipment is not an independently scheduled resource).
- No separate `staff_assignment_conflicts` or `booking_notes_history` tables (AR-09, AR-10: audit log covers both).
- No field-encryption columns yet — the FR-014 decision is pending (GAP G-09); adding a ciphertext column without a key-management decision would be fake security.

## 4. Data flow (summary — the DFD in Phase 7 follows this)

External entities: Owner, Manager, Receptionist, Accountant, Staff (all internal employees; no customer entity — customers never log in, SRS §1.1).

- **P1 Manage catalog & configuration** — Owner → halls/packages/services/event types/pricing/rules/settings → `halls`, `packages`, `services`, `pricing_rules`, `system_settings`.
- **P2 Customers & quotations** — Receptionist → customer + quotation → `customers`, `quotations`, `quotation_items`; reads availability from P4, writes no reservation.
- **P3 Bookings** — Receptionist/Manager → booking lifecycle → `bookings`, `booking_price_items`, `booking_policy_snapshots`, `booking_history`, `reschedule_history`, `booking_transfers`; reads availability, hours, maintenance.
- **P4 Availability** — reads `bookings`, `maintenance_blocks`, `hall_weekly_hours`, `hall_special_hours`, `halls`; returns available/unavailable + reason. No writes except the hold-expiry sweep.
- **P5 Contracts & files** — booking → `contracts`, `contract_versions`, `attachments` (+ bytes in `storage/uploads/`).
- **P6 Operations** — confirmed booking → `event_plans`, `tasks`, `task_history`; Manager assigns, Staff updates own tasks.
- **P7 Finance** — `payments`, `payment_corrections`, `installments`, `expenses`, `refunds`, `financial_policy_snapshots`, `cancellation_records`, `no_show_records`.
- **P8 Reporting & audit** — reads all stores; writes `audit_logs`, `notifications`, CSV exports to `reports/`.
- **P9 Backup** — Owner → `Connection.backup` → `storage/backups/` + `backup_records`.

## 5. Risks in this design and how Phase 1 de-risks them

| Risk | Phase 1 de-risk |
|---|---|
| Tier leakage (SQL in UI, rules in handlers) | Phase 1 builds only the three tiers with the import rules of §2; the first code review checks the boundaries before any feature code exists |
| Wrong password/session design is expensive to fix later | Phase 1 implements PBKDF2 + hashed tokens + lockout + audit now, with tests, before any booking code depends on auth |
| Schema churn after data exists | Schema v1 is written in full in Phase 1 (all 53 tables) so later phases add rows, not tables; migrations are versioned from day one |
| Availability logic built on a wrong time model | The time model (ISO-8601, venue timezone, half-open intervals, buffer) is fixed in Phase 1 config and models, even though the availability service lands in Phase 3 |
| Gradio/Linux quirks discovered late | Phase 1 ships a running Gradio app (login screen) on the real deployment setup, so environment issues surface in week one, not week ten |

## 6. Review conclusion

The SRS's proposed architecture is sound and matches the deck's 3-tier requirement exactly. With the adjustments recorded as AR-01–AR-14 (a `utils/` package, one permission module, both policy-snapshot tables kept, facilities as JSON, notes/conflicts via the audit log, three added columns, and a `schema_version` table), the design is complete enough to implement: 53 tables, 16 services, 12 view modules, and a transaction discipline that makes the double-booking rule enforceable in SQLite. The two open design questions (field encryption, PDF library) are decisions to be taken in Phases 1 and 4 respectively, not blockers to starting Phase 1.

*End of review — Phase 0, fresh solo restart, 2026-11-26.*
