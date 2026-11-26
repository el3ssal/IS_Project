# Wedding Hall Management System — Software Requirements Specification (As-Built)

**Project:** Wedding Hall Management System, Introduction to IS course
project (solo project, Ahmed Ashraf Elassal, Faculty of Computers and
Artificial Intelligence, Benha University)
**Version:** As-built v1.0 — final, 2026-11-26 (Phases 0–7 complete)
**Source SRS:** `Wedding_Hall_Management_System_SRS_and_AI_Agent_Brief.md`
(SRS v1.0, 2026-11-26). **Instructor deck:**
`Final_Project_Ideas_Full.pptx` (135 slides, created 2026-11-19).
**Status:** implemented and verified — 317 unittest + 3 console tests,
all passing; schema v4; 54 tables; 41 permissions; 5 roles.

This document describes the system **as built**, section by section
against the source SRS. Where the as-built system differs from the
source SRS, the difference is marked **[AS-BUILT]** with the decision
number from `docs/ASSUMPTIONS_AND_DECISIONS.md`. The instructor deck
outranks the SRS on conflict (source SRS §0.3); the deck contains no
deadline, rubric, grading weights, submission format or team rules —
none are invented here (open questions A-01/A-02, see §25).

## 0. Constraints (as built)

- **Stack:** Python 3.12, OOP, SQLite via stdlib `sqlite3`, SQL,
  Gradio UI, 3-tier architecture (`ui/` / `logic/` / `data/` in
  separate files). No React/Angular/Node/Django/Flask/MongoDB/database
  server/ORM/cloud/external service. `requirements.txt` is `gradio`
  only; pandas ships with Gradio and is used only for BarPlot charts.
- **Real persistence:** SQLite is the source of truth; no in-memory
  lists as system state. Tests use temporary databases.
- **Parameterized SQL everywhere;** no SQL built by concatenating
  user input. Foreign keys ON on every connection; WAL journal mode;
  `busy_timeout` 5000 ms.
- **Gradio runs fully offline** (decision A-36): analytics/telemetry
  disabled before the Gradio import; no Hugging Face account, no data
  sent. The app works with no network access.
- **Money:** integer piasters everywhere (1 EGP = 100); no FLOAT money.
- **Timestamps:** naive local ISO strings in the venue timezone
  (Africa/Cairo); UTC is never mixed in (A-37).

## 1. Product overview

A management system for one wedding hall in Benha: halls, packages,
services, pricing, customers, quotations, bookings, contracts, event
plans/tasks, staff, payments, installments, expenses, refunds,
reports, search, dashboards, calendar, backup/restore, bilingual UI.
The finished program has **no AI features for its users**.

**Out of scope (not built, per source SRS §1.4):** customer portal /
self-booking; online payment gateway; email/SMS/WhatsApp
notifications; mobile app / mobile-first layout; cloud hosting;
supplier/vendor management; inventory/purchasing/equipment stock;
attendance, payroll, full HR; tax/VAT; marketing segmentation,
loyalty, promo codes; customer reviews/complaints; electronic
signature service; two-factor authentication; AI/ML claims or
external AI services; equipment as independently scheduled resources;
waiting list (§9.4).

**[AS-BUILT — deferred, not built]:** FR-026 post-confirmation
service changes and FR-029 smart scheduling advisory are deferred
pending a scope decision (A-35, Phase 7 final status). They are
recorded as Deferred in the traceability matrix, not silently
dropped.

## 2. Users, roles, authentication

- **Roles (seeded, 5):** Owner, Manager, Receptionist, Accountant,
  Staff — with the responsibilities of source SRS §2.1.
- **Permissions as data (41 codes):** stored in `roles` /
  `permissions` / `role_permissions`; the Owner can configure each
  role's permissions; permission changes take effect without a code
  change (tested). Authorization is checked in every service
  operation via `require_permission()`; UI hiding is never the
  enforcement. Least privilege: only the Owner holds everything.
- **Login:** username + password. Passwords hashed with
  PBKDF2-HMAC-SHA256, unique random salt, 600,000 iterations in
  production (60,000 in tests only). Change-password and authorized
  reset (`user.manage`) workflows; enable/disable accounts; lockout
  after 5 failed logins for 15 minutes; failed attempts and security
  events audited. Sessions: secure random tokens, only the SHA-256
  hash stored server-side, 8-hour expiry, logout.
- **No "Remember Me"** (A-09): it cannot be done safely with this
  Gradio deployment mode, so it is omitted and documented.
- **Privacy:** National ID and sensitive data only for authorized
  roles; masked (`****` + last 4) unless the caller holds
  `customer.view_sensitive`; never in duplicate warnings, audit JSON
  or logs. Passwords, session tokens and full identity values are
  never logged (redacting log filter).
- **[AS-BUILT] No field-level encryption** (A-08): no vetted crypto
  dependency is in the approved stack and key management is
  unanswered; protection is RBAC + masking + redacted logging.
  Documented as a limitation, not claimed.

## 3. Core domain

- **Time model:** a booking has `start_at`/`end_at` (ISO-8601), one
  venue timezone (Africa/Cairo, from settings), used consistently.
- **Conflicts strictly forbidden (BR-02):** same-hall overlap is
  rejected with no manual override. The check considers confirmed
  bookings, active Pending holds, buffer time (60 min, A-18),
  maintenance blocks, closures/exceptional dates, hall active status,
  opening hours and minimum duration. Half-open `[start, end)`
  intervals. Creation runs in ONE `BEGIN IMMEDIATE` transaction that
  re-checks availability on the same connection immediately before
  insert (A-24) — proven by a two-thread concurrency test.
- **Lifecycle:** Inquiry (an interaction, not a booking) → Pending →
  Confirmed → In Progress → Completed, plus Cancelled, No-show,
  Expired. Single current status + full history (`booking_history`);
  `Rescheduled` is a timeline event, never a status.
- **Holds:** 48 h hold + 24 h grace (Owner-configurable settings).
  Pending reserves the time exclusively. If the deposit is not
  recorded by hold end + grace, the booking becomes Expired and the
  time is released (lazy expiry on access/sweep — no background
  process, A-22). Payment initiated before hold expiry may complete
  within the grace period; no backdating after expiry. Confirmation
  is automatic once the deposit is verified — no separate approval.
- **Quotations:** for a customer/hall/date/time/event type/package/
  guest count/services; itemised breakdown, validity, statuses
  Draft/Sent/Accepted/Expired. A quotation checks availability but
  does NOT reserve time; conversion re-checks availability; an
  accepted quotation converts to a Pending booking and starts the
  hold. Price snapshot preserved. No customer-rejection workflow.
- **Packages/services:** packages configurable by the Owner (name,
  base price, durations, included guests, suitable halls, included
  services, active status); hybrid model (extra services can be
  added). Services priced per event / per hour / per guest / by
  level; included services are listed at 0 and never double-charged
  (BR-12).
- **Pricing:** dynamic by hall, guest count, weekday/weekend, season,
  duration, package, services; clear itemised breakdown; extra guests
  at the package rate; manual discount (fixed or %) with reason +
  employee + configurable limit (seed 20%). No promo codes or
  loyalty points.
- **Snapshots:** price + policy snapshots (separate tables,
  `booking_policy_snapshots` and `financial_policy_snapshots`, A-05)
  are saved at confirmation; later config changes never alter a
  confirmed booking (BR-11).
- **[AS-BUILT] FR-029 scheduling advisory: NOT built** (deferred,
  A-35). The availability service is the hook for it.

## 4. Halls, hours, event types, maintenance

- Hall management: create/update/deactivate/configure (name,
  description, capacity, base price, status, facilities as JSON,
  suitable packages/services, weekly schedule, notes). Inactive halls
  cannot be booked; deactivation never deletes historical bookings
  (BR-23).
- Weekly opening schedule per hall (closed days, multiple intervals
  per day); the event must fit inside ONE interval (A-18).
- Exceptional dates (holidays, special closures/openings) override
  the weekly schedule.
- Event types: Wedding, Engagement, Birthday, Graduation, Private
  Events (seeded); the Owner can add/edit/deactivate; deactivation
  does not alter historical bookings.
- Maintenance: manual records (hall, type, description, start/end,
  recording employee); overlapping booking/quotation-conversion/
  reschedule is rejected (BR-05).

## 5. Customers

- Records: full name, primary phone, email, address, National ID,
  extra phones, internal notes, timestamps. Profile shows booking
  history, quotations, contracts, payments/refunds, past events,
  booking count, total contracted / collected / outstanding —
  computed from bookings/payments at read time, never stored, and
  kept distinct (A-30).
- Duplicate detection on normalized phone, National ID and email;
  confirmed duplicates block creation; possible matches shown for
  staff review (force flag); no automatic merge; full National ID
  never exposed in duplicate warnings.
- Interaction history (calls, inquiries, quotations, booking changes,
  cancellations, payments, contracts, notes) — internal only.
- One editable internal notes field per booking; every edit records
  old/new values in the audit log; additional contact phone stored
  (no login/permissions).

## 6. Contracts

- Contract templates are `system_settings` rows
  (`contract.template.<slug>`, JSON name+body; no `contract_templates`
  table by design, A-14/A-39). Templates use only placeholders from
  the allowlist.
- A contract is generated from customer + booking data, only from a
  Confirmed booking, with a unique number (`CT-YYYYMMDD-NNNN`) and
  the payment/cancellation/refund conditions in the template.
- **[AS-BUILT] Contracts are versioned HTML documents, not PDFs**
  (A-38): instructor permission for a PDF library (open question
  A-03) is still open, so no PDF library was installed. The rendered
  HTML is frozen in `contract_versions` and on disk under
  `storage/contracts/`, printable to PDF from the browser. Editing a
  signed contract creates a new version; old versions are preserved;
  a new version returns a signed contract to Pending Signature.
- Signature statuses: Draft / Pending Signature / Partially Signed /
  Signed / Cancelled, with enforced transitions. The signed copy is
  uploaded manually with a signature date; uploads are validated
  (extension allowlist PDF/JPG/PNG, 10 MB, magic-byte content check)
  and stored under a random name with sha256; downloads go only
  through an authorized handler. No in-app e-signature.
- The contract body contains the customer's full National ID by
  design (a legal document), visible only to `contract.view` holders
  and never written into audit JSON.

## 7. Staff and event operations

- Staff profiles: name, contact, username/account link, job role /
  specialties, active status, availability schedule, time off,
  assignment history. Employees ARE the login accounts (A-07) — no
  parallel employee concept. No leave-request workflow, attendance
  or payroll.
- Each Confirmed booking creates (or can generate) an Event Plan;
  task templates by event type and package; the system generates a
  checklist from templates (due = event start − offset); the Manager
  can edit the generated checklist for that event; later template
  changes never retroactively change existing tasks (generation
  copies items).
- Tasks: name, description, assigned employee, priority
  Low/Medium/High/Urgent, due date/time, status Pending / In
  Progress / Completed, timestamps; optional service link. The
  Manager creates/edits/assigns/reassigns; Staff views assigned
  tasks and updates only their own task status; status/assignment
  changes are recorded (`task_history` + audit). No task-cancellation
  workflow: the source SRS has none and the schema allows only the
  three statuses (A-42).
- Staff scheduling conflicts trigger a WARNING, not a hard block
  (BR-18): availability and overlapping assignments are checked
  before assignment; the Manager decides to continue or reassign;
  the decision + details are recorded in the audit log and a
  notification. Hall conflicts stay strictly blocked.

## 8. Financial management

- **Payments:** Cash, Bank Transfer, Visa/Card, InstaPay, Other
  (card/InstaPay are records of externally handled payments — no
  gateway). Fields: booking, amount, date/time, method, optional
  reference, recording employee, status, initiation timestamp,
  notes/attachment. A deposit or balance may be paid in several
  transactions with different methods. The deposit is a real payment
  row with an idempotency key (A-23); partial deposits accumulate and
  confirmation is automatic at the 30% threshold.
- **Immutability (BR-14):** payments are never edited in place.
  Corrections are full-amount Reversal/Void records (reason,
  employee, timestamp, original reference); the original is
  preserved (A-47). Idempotent confirmation: a repeated submission
  creates one payment.
- **Overpayments:** allowed against the current booking only; no
  general customer credit; shown separately; never counted as revenue
  beyond the contractual obligation; may become refundable under
  policy.
- **Policy:** Owner-configurable (`finance.*` settings: deposit %,
  installments count/interval, final deadline, late fees,
  cancellation tiers, no-show terms); the policy snapshot is saved
  at confirmation and later changes never alter existing confirmed
  bookings.
- **Installments:** one plan per Confirmed booking for the remaining
  balance; due dates evenly spaced to the final deadline (event − 7
  days); amounts split evenly with the remainder on the last;
  overdue detection is lazy; late fees are a computed display value
  only and are never auto-charged (A-46).
- **Expenses:** amount, date/time, category, description, method,
  recording employee, scope (venue / hall / event), attachment,
  approval Pending/Approved/Rejected, payment Pending/Paid kept
  separate. One row has exactly one scope, so a venue expense is
  never double-counted as an event expense (BR-24).
- **Accounting basis — cash basis (A-45):** revenue is recognised
  when a payment is recorded, capped at the contract value (an
  overpayment is a refundable liability, never revenue); refunds
  reduce revenue when PAID; profit = recognised revenue − approved
  expenses. Contract Value, Collected, Outstanding, Expenses,
  Refunds (only actually refunded), Cash Flow (by transaction date)
  and Profit are kept distinct and defined; reports state the basis.
- **Refunds:** separate transactions linked to the original payment
  + booking; workflow Pending Approval → Approved → Paid (or
  Rejected); multiple refunds per payment allowed but the total must
  not exceed the refundable amount — both caps (per-payment and
  per-booking refundable pool, BR-15) are enforced at request AND
  re-checked at approval; cancellable before Paid only; Paid refunds
  are corrected only by a new transaction; the original payment is
  never edited.
- **Cancellation:** fee + refundable computed from the
  confirmation-time policy snapshot; approval per policy /
  permissions (Manager/Owner); on approval the booking becomes
  Cancelled and the hall time is released immediately (BR-16, not
  after refund payment); a refund request is created automatically
  when money is due; contract/payments/history preserved;
  cancellation approval is separate from refund approval.
  **[AS-BUILT] Fee numbers are documented seed defaults** (A-43):
  ≥30 days before the event 10% of the total, 14–29 days 25%,
  <14 days 50%, capped at the amount paid. The source SRS defines
  the policy shape but no amounts; the Owner must confirm them with
  the hall.
- **Rescheduling:** one `BEGIN IMMEDIATE` transaction; validate
  hours, buffer, maintenance, closures, hall status and all active
  bookings; re-price with current rules (original manual discount
  carried forward, A-48); old/new prices preserved in
  `reschedule_history`; a price increase becomes outstanding with a
  due date (no payment collected); a price decrease makes the excess
  refund-eligible; multiple reschedules allowed; non-completed task
  due dates shift by the event delta; the contract is NOT
  auto-regenerated (the Manager creates a new version when required).
- **No-show:** any employee with booking-management permission may
  record it (reason optional, employee + timestamp recorded, added
  to Timeline and Audit Log). **[AS-BUILT] No-show numbers are
  documented seed defaults** (A-44): the deposit is forfeited,
  nothing is refundable, the unpaid balance stays outstanding.
- **Reopening:** Cancelled/Expired bookings cannot be reopened
  (BR-17); a new booking is created linked to the previous one
  (`previous_booking_id`); old contracts/payments do not transfer.
- **Ownership transfer:** to another customer with administrative
  approval (Manager, `booking.transfer`); old/new owner, reason,
  request/approval employee and timestamp preserved; a new contract
  version when required; payments stay attached, payer identity never
  rewritten.

## 9. Calendar and availability

- **Calendar (FR-057, as built in Phase 7, A-61):** unified view for
  all halls + per-hall view; Day / Week / Month as date ranges.
  Shows confirmed bookings, active Pending holds, maintenance
  blocks and closures/special hours. Detail panel: booking number,
  customer, hall, date/time, status, package, guests, price, paid,
  outstanding — money fields only with the matching permission.
- **[AS-BUILT] No drag-and-drop** (GAP G-13): Gradio has no native
  drag-and-drop calendar. Moving a booking from the calendar uses a
  date/time edit that runs the SAME rescheduling workflow as the
  Bookings screen (full revalidation, permissions, approval, clear
  rejection reason) — it never moves a booking directly (FR-058).
- **Availability search (FR-059):** pick hall/date/time; the price
  calculator reports available / not available with the reason
  (hours, closure, maintenance, buffer, existing booking).
- **Waiting list (§9.4):** explicitly not built.

## 10. Notifications

In-app only (no email/SMS/WhatsApp). Types: new booking, hold
nearing expiry, cancellation/reschedule, deposit/payment received,
installment due/overdue, event approaching, task overdue/due soon,
new task assignment, staff conflict warning, contract awaiting
signature, hall maintenance/closure. Role-based; the Owner
configures types/recipient roles/priority in `notification.config`;
read/unread, timestamp, priority, link to the record; RBAC enforced
when opening linked records. All triggers are wired (FR-060
complete). Firing is lazy (no background process, A-50/A-56),
de-duplicated at most once per booking/task per 24 h.

## 11. Attachments and file management

Attachments to customer/booking/contract/payment/expense: upload,
authorized download, preview (images, PDF — never arbitrary
HTML/SVG). Metadata in SQLite (original name, generated storage
name/path, MIME/type, size, category, upload date/time, uploader,
linked entity); bytes in `storage/uploads/` under random 32-hex
names, never served statically; categories Identity Documents /
Contracts / Receipts / Event Photos / Customer Documents / Other;
search + filters; configurable max size (seed 10 MB); extension
allowlist + magic-byte validation; no path traversal; authorization
on every preview/download/delete; permission-controlled, audited
soft-delete (`attachment.delete`, Manager/Owner); never executables.

## 12. Audit log and booking timeline

- Audit log: user, action, entity type + ID, timestamp, old value,
  new value, description/reason, context. Covers booking status
  changes, payment creation/reversal, refund approval/payment/
  cancellation, package price changes, role/permission changes,
  expense approval, hall config, customer identity edits, notes
  changes, ownership transfer, backup/restore, settings changes.
  Ordinary users cannot edit/delete entries (no such operation
  exists). Search/filter by user/date/entity/action
  (`audit.view`). No secrets in old/new JSON. The audit row is
  written in the same transaction as the critical change wherever
  possible, so a critical operation never commits without its audit
  record.
- Booking timeline: detailed per-booking history (old/new, actor,
  timestamp, action, reason) including rescheduling, notes, status,
  ownership, contract and financial changes; preserved after
  Cancelled/Expired. Each event appears once (timeline reads
  `booking_history` only, A-26).

## 13. Search, dashboards, reports

- **Advanced search** per module (customers by name/phone/email;
  bookings by number/customer/hall/date/status; contracts by
  number/customer/signature status; payments by transaction
  ID/method/date/amount; staff by name/specialty/tasks/status; files
  by name/category/customer/booking/date/employee) and **global
  search** across customers/bookings/contracts/payments/staff/events
  (events are bookings)/files. Multiple filters, sorting, pagination,
  RBAC-filtered results (amounts hidden without
  `booking.view_price`, National ID always masked, Staff sees only
  their own tasks; file RBAC is enforced in SQL so totals and pages
  match visible rows, A-52). Parameterized SQL; no restricted
  financial/identity data leaked through snippets.
- **Role dashboards:** Owner (revenue/profit/bookings/expenses),
  Manager (upcoming events, hall status, staff, tasks, conflicts,
  maintenance), Receptionist (new bookings, customers, quotations,
  holds nearing expiry), Accountant (payments, installments due,
  expenses, refunds, financial reports), Staff (assigned/upcoming/
  overdue tasks). The Owner controls widget availability per role
  (`dashboard.widgets` setting); no per-individual customization
  (A-53). **Pending bookings dashboard:** list, time remaining on
  hold, missing deposit, filters/warnings.
- **Reports (8):** booking, hall utilization, customer, package
  performance, staff performance, event, cancellation analysis,
  financial. Date filters, period comparison where meaningful,
  Gradio BarPlot charts, **CSV export for every report (mandatory)**,
  money as decimal EGP. No PDF/Excel export (A-03 still open).
  Role permissions enforced; metric formulas and date basis are
  stated on each report. Hall utilization counts weekly-schedule
  hours only — exceptional closures are not subtracted, so the figure
  is an upper bound (A-54).

## 14. System configuration

Owner-configurable settings (Settings tab, `settings.manage`):
venue name/contact, currency, default language + per-user preference,
date/time format + venue timezone, hold duration, grace period,
buffer time, minimum duration rules, booking/confirmation/
cancellation conditions, pricing rules, packages/services, discount
limits, roles/permissions, notification types/recipients/priorities,
attachment size/type rules, dashboard widgets, backup location.
Every value is validated (ranges, JSON shape, IANA timezone) and
audited (old/new). Config changes for future transactions never
rewrite historical snapshots, signed contracts, confirmed prices or
policy snapshots (BR-11). Hall hours/closures/exceptional dates are
managed on the Halls screens; pricing rules/packages/services on the
catalog screens; roles/permissions via the seeded data + staff
screens.

## 15. Backup and recovery

Manual backup/restore only (Owner, `backup.manage`). The Owner
creates a backup manually, views backups + timestamps, restores a
selected backup after explicit confirmation, and sees
success/failure feedback. Backups use SQLite `Connection.backup`
(never a blind copy of a live DB file). Backup metadata: timestamp +
SHA-256 checksum + size + schema version + a manifest (JSON sidecar)
with an inventory of `storage/` files. Restore validates integrity
before restoring (`PRAGMA integrity_check`, checksum match, schema
version match) and refuses corrupt or wrong-version backups; it
preserves a pre-restore safety copy and records it in
`backup_records` of the restored database; backup/restore is
audited; backup files are restricted to authorized users (authorized
download only, never a public directory). **[AS-BUILT] The backup
covers the database only** (A-62): uploaded file and contract
document bytes are inventoried in the manifest, not copied. No
high-availability claims.

## 16. Non-functional requirements

- **Performance (NFR-001, measured — `docs/PERFORMANCE.md`):**
  login, price calculation, dashboard load, booking create + deposit
  and booking/financial reports are all below their targets on both
  the seeded demo database and a generated 200-booking fixture
  (15 measurements, 0 over target; closest: financial report on 200
  bookings, 1.608 s vs a 2 s target). Gradio cold start to HTTP 200
  is ~9 s on this machine. Targets are not guarantees.
- **Reliability/concurrency:** transactions for multi-step
  operations; FKs on every connection; `busy_timeout` 5000 ms; WAL
  on the central host's local disk only (never a network share);
  preferred topology — DB on the central host's local disk, Gradio +
  DB on the same host, clients over LAN; short transactions; the
  booking conflict check is transactional (A-24).
- **Indexes** on frequent filters/joins (`bookings` hall/time,
  customer, status; `payments` booking; `tasks` assignee; audit
  entity/employee; notifications inbox; customers normalized
  phone/email; attachments entity). DB constraints for uniqueness
  and referential integrity.
- **Usability:** desktop/laptop-oriented; light background, calm
  colors, organized tabs, clear tables. **Bilingual Arabic/English:**
  Arabic RTL and English LTR, per-user language selection (Arabic
  default), navigation/buttons/messages/notifications/reports
  translated on the Phase 6 screens + login/welcome chrome and all
  dynamically rendered content. **[AS-BUILT] The static form labels
  of the Phase 1–5 screens stay English** (A-55); tab titles are
  bilingual. Unicode-safe storage/display throughout.
- **Local deployment:** one central computer runs Python + Gradio +
  SQLite; employee devices use a browser over the venue LAN; one
  shared database on the central host, never per-client databases;
  never multiple independent app instances against separate copies.
  `python main.py` serves 127.0.0.1:7860; `python main.py --lan`
  binds 0.0.0.0 with an explicit warning (restrict to the trusted
  LAN). Env vars: `GRADIO_SERVER_NAME`, `GRADIO_SERVER_PORT`,
  `WEDDING_HALL_DB`, `WEDDING_HALL_VENUE`, `WEDDING_HALL_CURRENCY`,
  `WEDDING_HALL_TZ`, `WEDDING_HALL_LANG`. No cloud hosting.
- **Maintainability:** clear module boundaries, type hints on core
  interfaces, docstrings on public service methods, consistent
  naming, no giant monolithic `main.py`, centralized configuration,
  structured logging with sensitive-data redaction, versioned
  schema/migrations (v1→v4), reusable validation and authorization,
  unit tests for rules + integration tests for DB workflows.
- **Testing:** Python stdlib `unittest`; temporary test database;
  deterministic fixtures; tests per phase; 317 tests + 3 console
  tests, all passing (see §21).

## 17. Architecture (as built)

3-tier, separate files. `wedding_hall_system/` package:

```text
config/    settings.py (AppSettings, env overrides), defaults.py (seed data)
data/      database.py (DatabaseManager), schema.sql, seed.py,
           migrations/ (001–004), repositories/ (one per aggregate)
logic/     models.py, permissions.py, validators.py, audit_service.py,
           auth_service.py, availability_service.py, pricing_service.py,
           hall_service.py, catalog_service.py, customer_service.py,
           quotation_service.py, booking_service.py, contract_service.py,
           task_service.py, staff_service.py, notification_service.py,
           payment_service.py, expense_service.py, refund_service.py,
           report_service.py, search_service.py, attachment_service.py,
           calendar_service.py, backup_service.py, settings_service.py,
           preferences.py
ui/        app.py, auth_views.py, catalog_views.py, booking_views.py,
           operations_views.py, finance_views.py, dashboard_views.py,
           search_views.py, files_views.py, reports_views.py,
           calendar_views.py, backup_views.py, settings_views.py,
           i18n.py, components/
utils/     exceptions.py (12 custom exception types),
           logging_config.py, validators.py
```

Tier rules: `ui/` imports `logic/` only (no SQL, no business rules);
`logic/` never imports Gradio and holds all business rules;
`data/` contains no business rules. Services: 20+ service classes
(see the class diagram, `docs/diagrams/class.mmd`). Models are
dataclasses with validation in `__post_init__`. Custom exceptions:
`ValidationError`, `AuthenticationError`, `AuthorizationError`,
`NotFoundError`, `BookingConflictError`, `HallUnavailableError`,
`InsufficientDepositError`, `PaymentError`,
`RefundLimitExceededError`, `DatabaseOperationError`,
`AttachmentValidationError` (+ base `ApplicationError`).

## 18. Database design (as built)

- **54 tables** (53 domain + `schema_version`), schema **v4**.
  Migrations: 001 initial, 002 (`contracts.signature_date`,
  `employees.specialties`), 003 (`reschedule_history.due_at`,
  `no_show_records.fee_amount`/`refundable_amount`), 004
  (`employees.language`). Fresh databases get the full schema from
  `schema.sql`; existing databases upgrade via migrations.
- Tables include: `roles`, `permissions`, `role_permissions`,
  `employees`, `employee_roles`, `employee_availability`, `sessions`,
  `login_attempts`, `system_settings`, `halls`, `hall_weekly_hours`,
  `hall_special_hours`, `maintenance_blocks`, `event_types`,
  `packages`, `package_halls`, `package_event_types`,
  `package_services`, `services`, `service_halls`, `pricing_rules`,
  `customers`, `customer_interactions`, `quotations`,
  `quotation_items`, `bookings`, `booking_services`,
  `booking_price_items`, `booking_policy_snapshots`,
  `financial_policy_snapshots`, `booking_history`, `discount_records`,
  `contracts`, `contract_versions`, `attachments`, `event_plans`,
  `task_templates`, `task_template_items`, `tasks`, `task_history`,
  `payments`, `payment_corrections`, `installments`, `expenses`,
  `refunds`, `cancellation_records`, `no_show_records`,
  `reschedule_history`, `booking_transfers`, `notifications`,
  `audit_logs`, `backup_records`, `schema_version`. Full DDL:
  `wedding_hall_system/data/schema.sql`; ER diagram (appendix):
  `docs/diagrams/er.mmd`.
- **Integrity rules:** PKs, FKs (ON), UNIQUEs (username, payment
  idempotency key, contract number), CHECKs (amounts ≥ 0, end >
  start, status enums, refund caps via service checks). Money is
  integer piasters; datetimes are ISO strings in venue time.

## 19. Core workflows (as built)

- **UC-01 Create customer** — Receptionist/Manager creates the
  record; duplicate detection on normalized phone/National ID/email.
- **UC-02 Create quotation** — Receptionist creates a quotation
  (price frozen); availability checked, time NOT reserved.
- **UC-03 Confirm booking** — accept quotation → convert to Pending
  booking (hold starts) → record the 30% deposit → Confirmed
  automatically.
- **UC-04 Modify confirmed booking** — **[AS-BUILT] deferred
  (FR-026).** Reschedule/transfer/cancel/no-show are built (§8);
  post-confirmation service changes are not.
- **UC-05 Reschedule** — Manager/Owner, one transaction, re-check +
  re-price, delta recorded, tasks shift.
- **UC-06 Cancel booking** — Manager/Owner; tiered fee from the
  frozen snapshot; hall released immediately; refund request
  auto-created.
- **UC-07 Record payment** — Accountant; idempotency key; split
  across methods; deposit accumulates to the threshold.
- **UC-08 Request refund** — Pending Approval → Approved → Paid;
  both caps enforced at request and approval.
- **UC-09 Assign task** — Manager assigns; warn-only conflict
  check; Staff updates only their own task status.
- **UC-10 Record expense** — record → approve/reject → mark paid
  (approval and payment separate).
- **UC-11 Backup and restore** — Owner creates/validates/restores
  backups; safety copy before every restore.

## 20. Business rules (all enforced)

BR-01 one hall per booking; BR-02 hall conflicts strictly
forbidden; BR-03 active Pending holds block the hall; BR-04 buffer
in availability validation; BR-05 maintenance/closure blocks
prevent booking; BR-06 guest count ≤ hall capacity; BR-07 duration
≥ package minimum; BR-08 quotation does not reserve a hall; BR-09
availability re-checked on quotation conversion; BR-10 deposit
verification confirms a Pending booking automatically; BR-11
historical confirmed price/policy snapshots immutable; BR-12
included services never double-charged; BR-13 — **[AS-BUILT]
deferred with FR-026** (service-change price recalculation is not
built); BR-14 payments immutable, corrections via reversal/void;
BR-15 refund total ≤ refundable amount; BR-16 cancellation releases
the hall on approval, not on refund payment; BR-17 Cancelled/Expired
cannot be reopened; BR-18 staff conflict warns, does not block;
BR-19 Staff updates only their own tasks; BR-20 permissions checked
in business logic; BR-21 attachments need authorization on every
access; BR-22 audit records for critical operations; BR-23
deactivation never destroys historical records; BR-24 venue expense
never double-counted as event expense; BR-25 no customer-rejection
workflow for quotations. (BR-13 is the only rule not enforced,
because its feature FR-026 is deferred — see §25.)

## 21. Testing and acceptance

- **Deck:** 3 console test cases including bad input
  (`tests/test_console.py`, 3/3 PASS).
- **SRS §21 (22 tests):** T-01 hall conflict; T-02 different hall;
  T-03 buffer; T-04 capacity; T-05 opening hours; T-06 pending
  hold blocks; T-07 expired hold releases; T-08 quotation does not
  reserve / conversion re-checks; T-09 minimum duration; T-10
  pricing snapshot; T-11 included service not double-charged; T-12
  manual service pricing; T-13 deposit confirms booking; T-14
  payment immutability; T-15 refund cap; T-16 cancellation releases
  hall while refund unpaid; T-17 staff conflict warns; T-18 RBAC
  (Staff cannot access another's task or financial reports); T-19
  audit old/new + actor; T-20 attachment authorization; T-21
  duplicate customer; T-22 backup restores and integrity verifies.
  All 22 are implemented and verified in the unittest suite.
- **Full suite (final):** `python -m unittest discover -s tests` →
  **317 tests, all pass** (Phase 1: 34; Phase 2: +44; Phase 3: +66;
  Phase 4: +35 incl. the migration-002 test; Phase 5: +51 incl. the
  migration-003 test; Phase 6: +47 incl. the migration-004 test;
  Phase 7: +40). Console 3/3. Every test uses a temporary database.

## 22. Error handling and logging

`try/except` at UI boundaries maps `ApplicationError` to
`gr.Error`/`gr.Info`; no raw tracebacks reach users. Friendly
messages exist for: hall unavailable, booking conflict, invalid
duration, capacity exceeded, insufficient deposit, permission
denied, refund limit exceeded, invalid file, database temporarily
busy. Unexpected exceptions are logged with context and no
sensitive data. Transactions roll back on failure; no orphaned rows
from partial multi-step workflows. Structured logging with a
redacting filter (passwords, tokens, full National IDs never
logged).

## 23. Diagrams and documentation

- Use Case, Activity, Sequence, DFD (context + Level 0 + Level 1
  for bookings) — `docs/diagrams/*.mmd` with rendered `.png`/`.svg`.
- Appendices: Class diagram, ER diagram (same folder).
- README (install, run, LAN, demo accounts, features, limitations),
  `docs/DEMO_SCRIPT.md` (timed 2-minute demo, verified),
  `docs/PERFORMANCE.md` (measured timings),
  `docs/REQUIREMENT_TRACEABILITY_MATRIX.md` (final counts §24),
  `docs/GAP_ANALYSIS.md`, `docs/ARCHITECTURE_REVIEW.md`,
  `docs/ASSUMPTIONS_AND_DECISIONS.md` (A-01–A-66).

## 24. Phases as delivered

Phase 0 requirements analysis; Phase 1 foundation (schema v1, auth);
Phase 2 halls/catalog/pricing; Phase 3 customers/quotations/bookings;
Phase 4 contracts/operations (schema v2); Phase 5 finance (schema
v3); Phase 6 files/search/dashboards/reports/bilingual UI (schema
v4); Phase 7 backup/LAN/performance/diagrams/documentation (no
schema change). Phase gates: no phase started without Ahmed's
approval.

## 25. Open questions and deferred items

1. **A-01 — Instructor acceptance of a wedding hall as a custom
   topic.** The deck lists 16 ideas; the wedding hall is not one of
   them (closest analogue: Idea 03 Hotel Booking Desk). Unconfirmed.
2. **A-02 — Submission date, format, grading weights.** Not in the
   deck. Unconfirmed.
3. **A-03 — PDF library permission.** Still open. Contracts are
   versioned HTML, printable to PDF from the browser; no PDF/Excel
   export.
4. **Real hall data** (A-05/A-06): hall names, capacities, prices,
   packages and services in the seed are clearly-marked EXAMPLES;
   the Owner must replace them with the real venue data.
5. **Cancellation/no-show fee numbers** (A-43/A-44): seed defaults
   (10%/25%/50% tiers; deposit forfeited on no-show). The Owner must
   confirm them with the hall.
6. **FR-026 post-confirmation service changes:** deferred pending a
   scope decision (BR-13 with it).
7. **FR-029 scheduling advisory:** deferred (A-35).

## 26. Definition of done (final)

- [x] All 7 phases delivered and verified by tests (317 unittest +
  3 console, all passing).
- [x] Deck requirements: 3-tier, 54 tables, 3+ classes + custom
  exceptions, CRUD + search, 2+ business rules in the logic tier,
  try/except + Info/Error, 1 CSV report (8 reports, all CSV),
  3 console tests, delivery = code + README + 4 diagrams +
  2-minute demo.
- [x] Traceability matrix final pass: every FR/NFR/AC/BR/T row
  marked Implemented/Verified, Partial, or Deferred with a reason.
- [x] No fake buttons, no mock financial transactions, no
  in-memory-only state; every visible control works against the
  real database.
- [ ] Open instructor questions (A-01/A-02/A-03) — outside the
  implementers' control; recorded, not assumed.
