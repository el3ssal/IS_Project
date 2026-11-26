# Assumptions and Decisions

Decisions taken during implementation. Each entry: what was decided,
why, and when it can be revisited. Nothing here silently changes an
approved business rule — see also `GAP_ANALYSIS.md`.

This is the FRESH solo restart (2026-11-26). The previous restart's
decisions (D-01–D-14) were reviewed; the still-valid ones are carried
over below with new numbering. D-01 (keep the team's
`src/course_project/` scaffold) is superseded: this restart follows
the clean `wedding_hall_system/` structure of SRS §17 /
ARCHITECTURE_REVIEW §2.

## Phase 1 (2026-11-26)

### A-01 — Clean solo structure: `wedding_hall_system/` (SRS §17)
No team scaffold exists in this restart. The package follows SRS §17
with the ARCHITECTURE_REVIEW adjustments (`utils/` for logging,
`logic/` holding models/exceptions/permissions/validators/services,
`data/` holding the manager, schema, seed and repositories). Tier
rules (ARCHITECTURE_REVIEW §2): `ui/` imports `logic/` only; `logic/`
never imports Gradio; `data/` contains no business rules.

### A-02 — Gradio for the UI
The instructor deck never mentions Tkinter; its pattern is browser
screens with `gr.Info`/`gr.Error` (GAP G-04). The SRS (§0.1, §17)
names Gradio explicitly. The UI is Gradio. Revisit only if the
instructor requires another technology — that would contradict the
deck pattern.

### A-03 — WAL journal mode: ON
`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`.
Safe because the DB lives on the central host's local disk and the
Gradio server is the only writer (SRS §16.2). Never point the DB at a
network share. Revisit if the deployment changes.

### A-04 — Money: integer piasters
All money is stored and computed as integer piasters (1 EGP = 100);
`config/settings.py::format_money` renders EGP in the UI. No FLOAT
money anywhere (ARCHITECTURE_REVIEW §2 rule 5).

### A-05 — Policy snapshots kept separate (ARCHITECTURE_REVIEW AR-06)
`booking_policy_snapshots` AND `financial_policy_snapshots` are
separate tables, as SRS §18.5 and §18.8 list them. They snapshot
different things (booking/hold conditions vs deposit/installment/
refund policy); merging would hide which policy a refund or
cancellation was computed under.

### A-06 — `discount_records` is its own table (ARCHITECTURE_REVIEW AR-07)
A discount has an employee, a reason, a limit check and an audit
trail, which a flat snapshot field cannot carry.

### A-07 — Employees are the login accounts
One `employees` row is both the login account and the staff profile
(GAP G-20). No separate `users` table.

### A-08 — No field-level encryption in v1
No vetted crypto dependency is in the approved stack and key
management is unanswered (GAP G-09, open question A-04). Sensitive
data is protected by RBAC, field masking, and never logging secrets.
This is a documented limitation, not a hidden gap. Revisit only with
an instructor-approved library and a key-management plan.

### A-09 — No persistent "Remember Me"
Sessions last up to 8 hours (browser session / expiry). A persistent
login cannot be done safely with the Gradio deployment mode in scope
(GAP G-10). No password or raw token is stored on the client; only a
SHA-256 hash of the token is stored server-side.

### A-10 — Seed defaults (changeable by the Owner in settings)
Hold 48 h, grace 24 h, buffer 60 min, deposit 30%, minimum duration
120 min, currency EGP, timezone Africa/Cairo, default language Arabic.
These are seed values, not business rules. Real hall values (number of
halls, capacities, prices) are seeded in Phase 2 after Ahmed confirms
them with the hall (GAP A-05/A-06).

### A-11 — Demo passwords
`owner`/`Owner123!`, `manager`/`Manager123!`,
`receptionist`/`Reception123!`, `accountant`/`Account123!`,
`staff`/`Staff123!`. Demo only; the Owner must change them before any
real use. They are documented in the README, not hidden.

### A-12 — Gradio / proxy quirk in this Linux VM
Gradio 6.32.0 fails to import when `NO_PROXY` contains bracketed IPv6
entries (`[::1]`): httpx raises `InvalidURL: Invalid port: ':1]'`.
And with `NO_PROXY` unset, Gradio's own startup check
(`GET /startup-events`) goes through the egress proxy and fails.
Workaround used here: set `NO_PROXY=no_proxy=localhost,127.0.0.1`
before launching (done in `main.py` / `__main__.py`). This does not
affect Windows, where those variables do not exist.

### A-13 — Auth reads go through the repository from the start
`auth_service` and `permissions` read employees/roles/sessions via
`data/repositories/employee_repository.py`; writes use
`DatabaseManager` helpers. No SQL lives in `ui/`, and no business rule
lives in `data/`.

### A-14 — Contract templates live in `system_settings` (no table)
ARCHITECTURE_REVIEW §3.5: templates are `system_settings` rows
(template name → content); versioning goes through
`contract_versions`. No `contract_templates` table.

### A-15 — Hall facilities as JSON
`halls.facilities_json` holds a JSON array (ARCHITECTURE_REVIEW
AR-08). Facilities are descriptive attributes, never queried by value,
so a join table adds cost without benefit.

### A-16 — Example seed catalog (Phase 2)
Real hall names, capacities, prices and opening hours are in NEITHER
source document (GAP A-05), so Phase 2 seeds clearly-marked EXAMPLE
data: "Main Hall (example)" (400 guests, 15,000 EGP/event, 12:00–23:00
daily), "Garden Hall (example)" (150 guests, 8,000 EGP/event), 5 event
types, 2 example packages, 8 services (SRS §3.6 catalog), Friday and
Saturday +10% weekend rules. Every example name carries "(example)".
The Owner can edit or deactivate all of it from the Halls & Pricing
screens. It must be replaced with real venue data before real use; it
is NOT presented as real anywhere (README, UI labels, this decision).

### A-17 — Pricing engine rules (Phase 2)
- Money is integer piasters; UI amounts are entered in EGP and
  converted with `egp_to_piasters` (max 2 decimals).
- Weekday convention: Monday=0 .. Sunday=6 (Python `datetime.weekday`).
  Weekend in Egypt is Friday (4) + Saturday (5).
- A pricing rule matches when every non-null criterion matches
  (hall/package/event type/weekday/season). Rules apply in creation
  order; percent rules apply to the running subtotal, fixed rules add
  or subtract directly. Positive value = surcharge, negative = reduction.
- Service pricing: per_event = unit × quantity; per_hour = unit ×
  ceil(duration/60) using the service duration or the event duration;
  per_guest = unit × guest count; by_level = unit × quantity (level).
- Package-included services are listed in the breakdown at 0 and never
  charged again (SRS §3.6, BR-12).
- Discounts are manual only (fixed or %), need a reason, and are capped
  by `pricing.max_discount_percent` (seed 20%). A fixed discount may not
  exceed the pre-discount subtotal.
- Duration validation: with a package, duration must be >= the package
  minimum; without a package, >= `booking.min_duration_minutes` (120).
  Capacity validation: guests must be <= hall capacity.
- `discount_records` rows are written at booking time (Phase 3), not by
  the price preview — the calculator only validates the discount.

### A-18 — Buffer policy (GAP A-06, SRS §4.2)
The EVENT itself must fit inside opening hours (one interval); the
buffer (60 min before and after, from settings) must not overlap
another booking, a maintenance block or a closure. Half-open intervals
`[start, end)` throughout, so a buffer ending exactly when a block
ends does not count as an overlap.

### A-19 — Deactivation, never hard delete (Phase 2)
Halls, event types, packages, services and pricing rules are
soft-deactivated (`is_active` / status), never hard-deleted, so
historical bookings keep their references (SRS §4.1, BR-23).
Maintenance blocks and special-hours rows are manual records and may
be deleted by a user with `hall.manage`.

### A-20 — No new permission codes needed in Phase 2
The Phase 1 codes cover Phase 2: `hall.manage`/`hall.view` for halls,
hours and maintenance; `catalog.manage`/`catalog.view` for event types,
packages, services and pricing rules; `booking.create` gates snapshot
creation (snapshots are written at booking confirmation, Phase 3).

### A-21 — Example customers and quotation seed (Phase 3)
Real customer data is in neither source document, so Phase 3 seeds
three clearly-marked EXAMPLE customers — "Ahmed Hassan (example)",
"Mona Ali (example)", "Karim Mostafa (example)" — plus one Draft
example quotation for Ahmed Hassan (Main Hall, Classic Wedding, next
Friday 18:00–23:00) so the quotation → booking flow is demoable.
All names carry "(example)". Replace with real data before real use
(GAP A-05/A-06).

### A-22 — Lazy hold expiry, no background process (GAP G-11)
There is no background process in this stack. An overdue Pending hold
is expired lazily: on every booking read/list (sweep) and inside every
booking write transaction, and the UI shows a hold countdown
("31h 12m left") computed from `hold_expires_at`. A booking past its
hold only flips to Expired when something reads or writes it.

### A-23 — Deposit is a real payment row (SRS §8.1–§8.2)
The deposit is recorded in `payments` (method Cash/Bank Transfer/
Visa/Card/InstaPay/Other, `idempotency_key` UNIQUE, `initiated_at` /
`confirmed_at`), never a mock. Confirmation is automatic once recorded
payments reach the 30% deposit: no separate booking-approval step
(SRS §3.4). Only the Accountant holds `payment.record`; the
Receptionist cannot record payments.

### A-24 — Double-booking prevention: BEGIN IMMEDIATE + in-transaction re-check (GAP G-12)
Booking creation runs inside one `BEGIN IMMEDIATE` transaction that
re-checks availability against `AvailabilityService` INSIDE the same
transaction, immediately before insert. There is no override path.
Proven by a two-thread concurrency test (separate connections, same
hall/slot): exactly one booking succeeds, the other raises
`BookingConflictError`, one row exists.

### A-25 — Number prefixes
Quotations `Q-YYYYMMDD-0001`, bookings `BK-YYYYMMDD-0001`, payments
`PAY-YYYYMMDD-0001` (daily sequence, counted per day).

### A-26 — Timeline shows each event once (SRS §12.2)
`BookingService.timeline()` returns `booking_history` rows only. The
audit log keeps its own copy of every event in the same transaction,
but merging the two into the timeline duplicated every entry
(`booking.created` alongside `created`), so the merge was removed.

### A-27 — Cancellation needs Manager/Owner (SRS §8.8)
`booking.cancel` is held by Manager/Owner in the Phase 1 role map; the
Receptionist does not hold it. SRS §8.8 says an authorized employee
records the request and "approval follows policy and permissions" —
at this stage the cancel action itself is the approval, so it stays
with Manager/Owner. The Receptionist's cancel attempt is rejected
with `AuthorizationError` (tested).

### A-28 — Snapshots accept `booking.create` OR `payment.record`
`PricingService.create_snapshots` previously required
`booking.create` only, which the Accountant does not hold — so a
deposit recorded by the Accountant could not freeze the policy
snapshots at confirmation. The gate now accepts either permission:
snapshots are created as part of booking creation (Receptionist) or
as part of deposit confirmation (Accountant).

### A-29 — Package minimum duration overrides the setting
`AvailabilityService.validate_event_window` uses the package's
`min_duration_min` when a package is chosen (Classic Wedding: 180
min) and falls back to `booking.min_duration_minutes` (120) without
one. Tests must use windows that clear the package minimum when they
mean to test hours/overlap rather than duration.

### A-30 — Profile totals are computed, never stored (SRS §5.1)
Contracted / collected / outstanding are computed from bookings and
recorded payments at read time; the three values are kept distinct
and never derived from each other.

### A-31 — Cancellation fee at this stage (Phase 5 completes it)
Phase 3 `cancel_booking` records fee 0 and refundable = deposit paid
so far. Fee computation from the confirmation-time policy snapshot
and the refund workflow arrive in Phase 5 (SRS §8.8).

### A-32 — National ID masking rules (FR-012)
The service masks the National ID (`****` + last 4) unless the caller
holds `customer.view_sensitive`; the UI never unmasks it. The full
value never appears in duplicate warnings or in audit old/new JSON
(identity edits are recorded as "changed").

### A-33 — Sending an expired quotation is rejected (bug fix)
`send_quotation` previously did not check validity, so an
expired-by-time Draft could be sent. It now lazily expires (like
`accept_quotation`) and raises `ValidationError`.

### A-34 — Timestamps stored as ISO strings with "T"
Repositories store datetimes as `datetime.isoformat()` strings
(`2026-12-04T18:00:00`). Raw SQL in tests or tools must compare
against the same format — a `datetime` parameter bound by the
deprecated sqlite3 adapter (`2026-12-04 18:00:00`, space separator)
matches nothing.

### A-35 — FR-029 scheduling advisory deferred
The Phase 2 RTM note listed the scheduling advisory as Phase 3 scope,
but the approved Phase 3 scope (customers, quotations, bookings) did
not include it. Deferred to a later phase; the availability service
remains the hook for it.

### A-36 — Gradio runs fully offline (follow-up, 2026-11-26)
Gradio is a Hugging Face product and its bundled `huggingface_hub`
otherwise contacts huggingface.co (version / telemetry traffic the
project does not need). `main.py` and `wedding_hall_system/__main__.py`
now set `GRADIO_ANALYTICS_ENABLED=False`, `HF_HUB_OFFLINE=1` and
`HF_HUB_DISABLE_TELEMETRY=1` via `os.environ.setdefault` BEFORE the
Gradio import. `setdefault` (not a forced set) so a user can opt back
into online mode by exporting the variables first. Verified by smoke
test after the change: the app still starts and serves HTTP 200, Owner
login works, and the receptionist flow runs end to end.
(Note: recorded as A-36, not A-21 — the follow-up asked for "next
after A-20", but A-21–A-35 were already taken by the Phase 3 decisions
above, recorded earlier the same day.)

### A-37 — Booking timestamps share one clock (bug fix, 2026-11-26)
`bookings.created_at` used SQLite's `DEFAULT CURRENT_TIMESTAMP` (UTC)
while `hold_expires_at` / `grace_expires_at` were computed in Python
from naive local time — so hold − created read 50h instead of 48h in
Africa/Cairo (UTC+2), caught by `test_create_booking_pending_with_hold_and_deposit`.
Fix: `BookingService.create_booking` passes `created_at` explicitly
from the same `now` used for the hold/grace computation; the repository
falls back to naive local `datetime.now()` when no value is given.
Same latent skew exists wherever a duration is computed against a
`DEFAULT CURRENT_TIMESTAMP` column — fix the same way if a test
exposes it elsewhere; do not mix UTC and naive-local timestamps.

### A-38 — Contracts are versioned HTML, not PDFs (2026-11-26)

Instructor permission for a PDF library (GAP A-03) is still open, so
no PDF library was installed and `requirements.txt` is unchanged.
A contract is a versioned HTML document rendered from its template
with the booking's data frozen at generation time, stored in
`contract_versions.body` and as a file under
`storage/contracts/<number>_v<n>.html`, printable to PDF from the
browser. A new version (re-render or edited body) preserves all old
versions; a signed contract returns to `pending_signature` when a new
version is created, because the new version is not signed. If a PDF
library is permitted later, PDF export is a small addition on top of
the same rendering (the template + frozen body already exist).

### A-39 — Contract templates are settings rows, not a table (2026-11-26)

By design (ARCHITECTURE_REVIEW §3.5): templates live in
`system_settings` as `contract.template.<slug>` → JSON
`{"name", "body"}`. Template bodies may only use placeholders from
`models.CONTRACT_PLACEHOLDERS` (unknown placeholders are rejected at
save time). A generated contract stores `template_name` only; deleting
a template in use by a contract is rejected. The seeded default
template's key is the slug of its name
(`contract.template.standard_wedding_contract`); databases seeded
before Phase 4 stored it under `contract.template.standard`, and the
seed renames that row instead of creating a duplicate.

### A-40 — Signed-copy uploads: validate, random name, authorized download only (2026-11-26)

Signed contracts are uploaded manually (SRS §6): extension allowlist
(PDF/JPG/PNG), 10 MB limit (`attachments.max_size_mb`), and magic-byte
content check (a `.pdf` that is not a PDF is rejected). Files are
stored under `storage/uploads/` with a random 32-hex-character name;
metadata + sha256 go in the `attachments` table. Downloads go only
through `ContractService.download_attachment`, which requires
`attachment.download` and verifies the resolved path stays inside the
uploads directory (GAP G-15 — the directory is never served
statically). Uploading a signed copy marks the contract Signed with
the recorded signature date. The contract body contains the
customer's full National ID by design (a contract is a legal
document); it is only visible to `contract.view` holders and the ID
is never written into audit JSON (A-32 pattern).

### A-41 — Two permission-code changes for Phase 4 (2026-11-26)

`task.view` did not exist as a code (only `task.view_assigned` /
`task.update_own`), so it was added — "View all tasks, event plans
and task templates" — and granted to Manager (Owner gets everything).
`user.manage` already existed but only the Owner held it; it is now
also granted to Manager, with its Phase 4 scope limited to staff
profile fields (name, phone, email, specialties). Account
activation/deactivation and role changes stay Owner-only, and nobody
may change their own roles or deactivate their own account. Both
changes are data (seeded `INSERT OR IGNORE`), so existing databases
pick them up on the next app start. The Manager also holds
`task.manage`; staff status updates require `task.update_own` plus
being the assignee.

### A-42 — No task-cancellation workflow (2026-11-26)

The SRS has no task-cancellation workflow (cancellation in the SRS is
a booking concept), and the Phase 1 schema restricts `tasks.status`
to Pending / In Progress / Completed. A task that should not be done
stays Pending; a completed task is final and cannot be edited or
reassigned. The pre-existing `contract_repository.insert_contract`
hardcoded new contracts as `pending_signature`, contradicting the
schema DEFAULT `draft` and the SRS signature statuses — fixed to use
the default, so a generated contract starts as Draft. The
pre-existing `notification_repository.list_inbox` had an AND/OR
precedence bug (the unread filter bound only to the role branch) —
fixed with parentheses. Task templates carry no persisted link to
generated plans (`event_plans` has no template column): generation
copies items into `tasks`, so deleting a template never affects
existing plans and no in-use check is possible or needed.

### A-43 — Cancellation fee tiers are documented seed defaults (2026-11-26)

The SRS defines the cancellation policy shape (§8.4: "refund policy
by cancellation timing", §8.8: fee + refundable from the
confirmation-time snapshot) but gives NO amounts anywhere, and the
deck has no numbers either. So the fee is Owner-configurable
(`finance.cancellation_policy`, JSON tiers) with these seed defaults:
≥30 days before the event 10% of the total, 14–29 days 25%, <14 days
50%. The fee is capped at the amount paid, so fee + refundable always
equals the amount collected (no extra charge beyond what was paid).
The tiers are frozen in the financial snapshot at confirmation; later
changes never rewrite a confirmed booking's fee. The Owner must
confirm these numbers with the hall before real use — they are
defaults, not SRS rules. A Pending booking has no snapshot and no fee
(refundable = deposit paid, Phase 3 behaviour preserved).

### A-44 — No-show policy defaults (2026-11-26)

Same gap as A-43: the SRS requires an Owner-configurable no-show
policy (§8.10) with no numbers. Seed default
(`finance.no_show_policy`): the deposit is forfeited (fee =
deposit paid), nothing is refundable, and the unpaid balance stays
outstanding. Frozen in the financial snapshot at confirmation; the
fee/refundable outcome is stored on the `no_show_records` row
(migration 003).

### A-45 — Simplified accounting basis: cash basis (2026-11-26)

GAP G-18 resolved for this academic system: revenue is recognised
when a payment is recorded (cash basis); recognised revenue is capped
at the contract value, so an overpayment is a refundable liability,
never revenue (FR-047); refunds reduce revenue when PAID (pending or
approved refunds are not cash outflow, SRS §8.6); profit =
recognised revenue − approved expenses. Expenses count when approved
(approval is the commitment); the paid total is reported alongside.
The basis is stated on the reconciliation screen and in the README.
Date windows filter by event date (contract value), payment
confirmation date (collected), refund payment date (refunds) and
expense date (expenses).

### A-46 — Installment plans: created once, evenly spaced, no auto-charging (2026-11-26)

A plan is created once per Confirmed booking for the remaining
balance (total − net collected); a second plan is rejected so paid
installments are never rewritten, and a reschedule does not rewrite
an existing plan (the difference is outstanding). Due dates are
spaced evenly between creation and the final deadline (event start
minus `finance.final_deadline_days_before_event`, seed 7 days); when
the deadline has already passed, every installment is due
immediately. Amounts split evenly; the last takes the remainder.
Overdue status is lazy (marked on read / Finance due sweep, GAP
G-11). Late fees (`finance.late_fee_percent`, seed 0%) are a
computed display value only — the system never auto-charges a late
fee (SRS §8.9: do not automatically collect payment).

### A-47 — Payment corrections reverse the whole payment (2026-11-26)

A Reversal/Void record always reverses the full payment amount
(the schema's `payment_corrections.amount` is the payment's amount).
A partial correction is a full reversal followed by a new correct
payment. This keeps `sum(recorded)` arithmetic exact and the audit
trail unambiguous. The payment status after a reversal is `reversed`
(the correction type is `reversal` — the two words differ and the
schema CHECK only accepts `reversed`).

### A-48 — Reschedule: discount carried, tasks shift, contract not auto-regenerated (2026-11-26)

Re-pricing carries the booking's original manual discount forward
(read from `discount_records`), so a reschedule never silently drops
a discount the Manager granted. Non-completed task due dates shift
by the same delta as the event start (with `task_history` rows). The
contract is NOT regenerated automatically: the Manager creates a new
contract version when required (via `ContractService.create_new_version`).
Price items are replaced with the new breakdown; old/new totals stay
in `reschedule_history` (plus `due_at` for an increase).

### A-49 — A deposit reversal does not de-confirm a booking (2026-11-26)

The SRS has no de-confirmation rule. Reversing a deposit after
confirmation recomputes `deposit_paid` and outstanding but leaves the
status Confirmed; the shortfall is flagged as a reconciliation
discrepancy ("deposit shortfall") instead of silently changing the
booking's commercial state.

### A-50 — Remaining notification triggers fire lazily (2026-11-26)

No background process exists (GAP G-11), so the Phase 5 triggers
fire from read paths: hold-expiring (hold ends within 6h) and
event-approaching (event within 48h) fire from the booking read sweep
(`BookingService.sweep_expired_holds`); installment-due fires when an
installment becomes overdue on read or from the Finance due sweep.
Each fires at most once per booking per 24h (de-duplicated on the
notifications table; `created_at` is UTC while the window uses local
time, so the effective window is ~22h — harmless for a warning).
`new_booking`, `payment_received`, `booking_cancelled` and
`booking_rescheduled` fire inside their own transactions. Still
unwired: `task_due_soon`/`task_overdue` (needs a task due-date sweep)
and `hall_maintenance` (Phase 6).

## Phase 6 (2026-11-26)

### A-51 — `attachment.delete` is a new permission code (2026-11-26)

Deletion of a general file needs its own code: the existing codes do
not cover it cleanly (`contract.manage` only fits contract files).
`attachment.delete` is granted to Manager (+ Owner via everything);
Receptionist and Accountant can upload/download but cannot delete.
Deletion is a soft-delete (metadata flagged, bytes kept) with an audit
row, the Phase 4 pattern. Seeded as data (`INSERT OR IGNORE`), so
existing databases pick it up.

### A-52 — File-search RBAC happens in SQL, not after pagination (2026-11-26)

Filtering file-search rows in Python after LIMIT/OFFSET would make
the total and the pages lie (rows the caller may not see would still
count). `search_repository.search_files` takes an `entity_types` list
of the types the caller may view, so the COUNT and the page both
reflect visible rows only.

### A-53 — Dashboard widgets: Owner-controlled per role (2026-11-26)

Widget availability is the `dashboard.widgets` system setting (JSON
role → widget keys, seeded from `DEFAULT_DASHBOARD_WIDGETS`); the
Owner edits it through `ReportService.update_dashboard_widgets`
(`settings.manage`). A widget is shown only when the Owner enabled it
for the caller's role AND the caller holds the permission its data
needs (e.g. revenue needs `finance.view`). No per-individual
customization (SRS §13.2). A widget whose data the caller may not see
is omitted silently — never an error.

### A-54 — Hall-utilization basis: weekly schedule only (2026-11-26)

Available hours come from the hall's weekly opening schedule over the
window; booked hours are active bookings clipped to the window.
Exceptional closures / special hours are NOT subtracted — computing
them would need a day-by-day override resolution the SRS does not
specify, so the figure is an upper bound on true availability and the
report's basis string says so. A Pending hold does not count as
booked: only Confirmed / In Progress / Completed bookings do.

### A-55 — Bilingual scope: Phase 6 screens fully, legacy labels stay English (2026-11-26)

SRS §16.3 wants Arabic/English for core screens. The Phase 6 screens
(Dashboard, Search, Files, Reports, Audit) plus the login/welcome
chrome and all dynamically rendered content are fully bilingual with
RTL/LTR switching (`ui/i18n.py`, `dir` wrappers). The static form
labels of the Phase 1–5 screens stay English in this phase: Gradio
builds labels once, and renaming every legacy input through the
toggle would be a large refactor of tested UI code for no new
functionality. Tab titles are bilingual
("Dashboard • لوحة المتابعة") because Gradio cannot rename a tab at
runtime. Per-user language is stored on `employees.language`
(migration 004, default 'ar'); the venue default is the
`venue.default_language` setting (seeded 'ar').

### A-56 — Task-due sweep is lazy and de-duplicated like A-50 (2026-11-26)

`TaskService.sweep_task_notifications` runs from the `list_tasks`
read path (best-effort, like the booking hold sweep): tasks due
within 24h fire `task_due_soon` to the assignee, past-due incomplete
tasks fire `task_overdue` (high priority). Each fires at most once
per task per 24h, de-duplicated on `notifications.created_at` (UTC)
against a local-time window — the same ~22h effective window as A-50.
`hall_maintenance` fires inside `add_maintenance` (role-targeted to
Manager via `notification.config`). FR-060 is now fully wired.

### A-57 — Global-search "events" are bookings (2026-11-26)

SRS §13.1 lists Events as a global-search scope, but the system has
no separate event entity — an event IS a booking (hall + date +
event type). Global search returns the booking rows a second time
under the `events` key so the UI can show an Events section. Staff
global search returns only their own tasks (by name) — no customers,
bookings, payments, contracts or staff rows, which they have no
permission to see.

### A-58 — CSV money is decimal EGP; charts are Gradio BarPlots (2026-11-26)

CSV cells carry money as decimal EGP numbers (`44000.00`, no currency
suffix) so the file opens correctly in a spreadsheet. Charts use
Gradio's built-in `gr.BarPlot` over pandas DataFrames built in the UI
layer only — pandas ships with Gradio, so `requirements.txt` stays
gradio-only and the logic tier stays dependency-free. There is no
PDF/Excel export: CSV is the mandatory academic export, and A-03
(PDF library permission) is still open.

### A-59 — FR-063 timeline was already implemented in Phase 3 (2026-11-26)

The RTM row for FR-063 (Booking Timeline) still said "Planned", but
`BookingService.timeline()` (booking_history, preserved after
cancellation) was implemented and tested in Phase 3
(`test_bookings.py`: timeline survives cancellation). The RTM row is
corrected to Implemented + verified in the Phase 6 update — no code
changed for this row.

### A-60 — FR-060 is now fully implemented (2026-11-26)

All notification types in SRS §10 are wired: the Phase 4/5 triggers
plus `task_due_soon` / `task_overdue` (A-56) and `hall_maintenance`
(fires on maintenance creation). Verified in
`test_files_search_reports.py` (sweep counts, de-duplication,
Manager inbox). FR-062's audit search/filter screen is also done
(`audit_service.search`, `audit.view`); the audit write side was
Phase 1–5 work (T-19).



### A-61 — Calendar is a table view; reschedule reuses the booking workflow (2026-11-26)

Gradio has no native drag-and-drop calendar (GAP G-13), so the
Phase 7 calendar (`CalendarService`, `ui/calendar_views.py`) is a
Day/Week/Month date-range table of bookings, active Pending holds,
maintenance blocks and closures/special hours, with a detail panel
and a date/time edit form. Changing a booking's time from the
calendar calls `BookingService.reschedule_booking` — the SAME
workflow as the Bookings screen (full revalidation, permissions,
price recalculation) — never a direct move (SRS §9.2, FR-058).
Money fields in the list/detail are gated by `booking.view_price`
and `payment.view`; Staff holds no `booking.view` and cannot open
the calendar at all.

### A-62 — A backup covers the database only (2026-11-26)

`BackupService.create_backup` copies the database with
`Connection.backup` and writes a manifest JSON sidecar (checksum,
schema version, created-by, and an inventory of the files under
`storage/contracts/` and `storage/uploads/`). The uploaded file and
contract document BYTES are not copied into the backup: they live
outside the database, and zipping them would change the backup
format the SRS prescribes (SQLite backup + checksum). The manifest
lets the Owner see what existed at backup time. Restore therefore
restores the database, not the file bytes — recorded here and in
the README so it is never a silent gap.

### A-63 — Restore replaces the live file; safety copy survives in the restored DB (2026-11-26)

Restore validates first (file exists, SHA-256 matches the record,
`PRAGMA integrity_check` is ok, schema version equals the app's),
then makes a safety copy of the live database, checkpoints the WAL,
replaces the live file, re-initializes and re-validates. Because the
live file is replaced, the pre-restore `backup_records` rows are
replaced by the backup's own rows — so the safety copy is
re-recorded into the restored database (and its audit row lands
there too). Connections are short-lived in this app, so no live
connection holds the file open during the swap.

### A-64 — LAN mode is an explicit flag, loopback is the default (2026-11-26)

`python main.py` serves 127.0.0.1:7860. `python main.py --lan`
binds 0.0.0.0 and prints a warning that the app is reachable from
every device on the network (restrict to the trusted venue LAN,
SRS §16.4). `--host` / `--port` override the address; env vars
`GRADIO_SERVER_NAME` / `GRADIO_SERVER_PORT` remain the defaults.
Verified on this VM: loopback HTTP 200 in ~8 s, LAN mode HTTP 200
in ~6 s bound to 0.0.0.0.

### A-65 — Performance measured, no fix needed (2026-11-26)

`scripts/perf_check.py` measures the NFR-001 targets on the seeded
demo database and on a generated 200-booking fixture (results in
`docs/PERFORMANCE.md`): 15 measurements, 0 over target. Closest is
the financial report on 200 bookings (1.608 s vs a 2 s target — it
recomputes per-booking reconciliation rows, so it grows with booking
count; 0.017 s on the demo database). No re-architecture and no
optimization was done: the numbers pass as measured. Gradio cold
start to HTTP 200 is ~9 s on this machine (first start; A-12).

### A-66 — FR-026 and FR-029 stay deferred at project end (2026-11-26)

Post-confirmation service changes (FR-026) and the scheduling
advisory (FR-029) were never in an approved phase scope. At the end
of the final phase they remain Deferred in the traceability matrix
with reasons, not silently dropped — and BR-13 (service changes
recalculate price and record the delta) is therefore the one
business rule not enforced, because its feature does not exist.
Building either would be new scope needing Ahmed's approval.

### A-67 — Diagrams are rendered locally from Mermaid sources (2026-11-26)

The 4 deck diagrams (Use Case, Activity, Sequence, DFD) plus Class
and ER appendices live in `docs/diagrams/` as `.mmd` sources with
rendered `.png` and `.svg` produced by a local
`@mermaid-js/mermaid-cli` via npx (local Puppeteer/Chromium —
nothing uploaded anywhere). Every relationship in the ER is a real
FK in `schema.sql`; the ER draws the core 26 entities and lists the
other 28 tables in its header comment. Phase 7 services
(`BackupService`, `SettingsService`, `CalendarService`) and their
tabs were added to the use-case, DFD Level 0 and class diagrams in
the Phase 7 update; the activity, sequence, DFD Level 1 and ER
diagrams are unchanged by Phase 7 (no schema change, no new
booking flow).

### A-68 — Field-level encryption covers the National ID only (2026-11-26)

SRS §2.4 names the National ID as the sensitive field to encrypt at
rest, so FR-014 encrypts exactly that field (Fernet, `enc:v1:` prefix,
blind SHA-256 index for exact-match duplicate detection, since Fernet
is randomized). Phone and email are NOT encrypted: they are lookup
keys (normalized-phone duplicate detection, find-by-phone/email) and
are protected by RBAC and masking exactly as before. Decryption is
transparent to authorized roles and masked for everyone else;
audit logs never contain the plaintext value. The key comes from
`WEDDING_HALL_ENCRYPTION_KEY` (env var, never committed, never in
the DB); `scripts/generate_encryption_key.py` generates it; a missing
or invalid key fails loudly with a clear message and never silently
stores plaintext for new writes.

### A-69 — Post-confirmation service changes apply to Confirmed bookings only (2026-11-26)

FR-026 changes apply to Confirmed bookings. Pending bookings are
rejected (their price is not frozen yet — re-price them by editing
the quotation/booking instead) and In Progress bookings are rejected
(the event is being delivered, not re-priced). The change re-prices
with the current rules carrying the original manual discount forward
(same pattern as rescheduling, A-48); an increase becomes outstanding
with a due date (event − final-deadline days); a decrease creates a
`pending_approval` refund for the refundable part (linked to the
largest recorded payment) — never auto-paid. Old/new selections,
prices, user, time and reason are recorded in
`booking_service_changes`, the booking timeline and the audit log.

### A-70 — A service change creates a new contract version only when the total changes (2026-11-26)

If the service change moves the total amount (delta ≠ 0) a new
contract version is generated after the change commits (same pattern
as ownership transfer); a zero-delta service swap is recorded as a
note only, with no new version. Previous versions are preserved in
both cases.

### A-71 — Service-layer exception messages stay English (2026-11-26)

All user-visible UI strings are bilingual, but `raise`d service
exception messages (ValidationError, NotFoundError, PermissionError,
etc.) remain English. They are developer-facing diagnostics rendered
through `gr.Error`; translating every exception at the service layer
would couple the business logic to the UI language. The UI surfaces
its own translated messages for the common failure paths.

### A-72 — Remember Me: 30-day hashed tokens, rotation, revocation (2026-11-26)

FR-011 is implemented with server-side persistent tokens: a random
opaque token, only its SHA-256 hash stored, 30-day expiry, at most 3
live tokens per employee (oldest revoked first), rotation on every
successful validation (the old token dies), revocation on logout and
on any password change/reset or account deactivation, issuance and
revocation audited without the token value. The client handoff uses
Gradio 6.32 `gr.BrowserState` (browser localStorage, storage key
`whms-remember-me`); the password is never stored on the client.
The BrowserState `secret` is only obfuscation for localStorage — the
real security is server-side. `WEDDING_HALL_BROWSER_SECRET` is an
optional stable secret: without it Gradio generates a fresh secret
per process, so a server restart simply asks the user to log in
again. Sessions without Remember Me still last up to 8 h.

### A-73 — Scheduling advisory: deterministic, advisory only (2026-11-26)

FR-029 suggests the nearest feasible alternatives when the requested
hall/date/time is unavailable, in a fixed deterministic order: same
hall same day nearest time first (earlier wins ties), then same hall
later days at the same time of day, then other halls by id, then
shorter-duration options. Candidates are checked in 30-minute steps
up to 7 days ahead, at most 5 suggestions, each verified with
`check_availability` plus hall capacity, each with a plain-language
reason in the current language. It never changes a booking, never
shortens without customer agreement, and never goes below the
package minimum (a request shorter than the minimum returns no
suggestions). The service stays language-neutral (`reason_key` +
params); the UI renders the reason.

### A-74 — Seed stored National IDs in plaintext; corrected (2026-11-26)

The Phase 8 customer-repository encryption was verified, but the
seed (`data/seed.py`) inserted example customers with raw SQL and
plaintext National IDs — the earlier claim that "seed inserts via
the repository" was wrong. Fixed: the seed now encrypts with
`crypto.encrypt_text` + `blind_index` exactly like the repository,
and raises `ConfigurationError` without a key instead of silently
storing plaintext. Verified on a fresh database: seeded IDs are
stored as `enc:v1:…` with 64-char hashes.

### A-75 — Notification `data_json` must never be NULL (2026-11-26)

Migration 005 made `notifications.data_json TEXT NOT NULL DEFAULT
'{}'`, but `insert_notification` passed an explicit NULL when no
i18n keys were given, breaking every key-less `notify()` call.
Fixed in the repository: store `'{}'` when `data_json is None`.
Notification i18n keys are stored as JSON (`title_key`, `body_key`,
`params`) and translated at read time; the stored English title/body
remain as a fallback for older rows.

### A-76 — Language toggle: one merged `gr.update` per component; Buttons use `value`, not `label` (2026-11-26)

Gradio rejects the same component twice in one event's outputs, so
`app.py` merges every registry target into ONE `gr.update` per
component for the toggle. A live bug proved the rule: the login
chrome put two `gr.Button`s in the "label" bucket and calling the
toggle raised `AppError: Button.__init__() got an unexpected keyword
argument 'label'` — the whole event errored and nothing switched.
Buttons show text via `value`, so they belong in the "value" bucket;
the registry itself was correct. Fixed and verified live (English
then Arabic) plus a regression test that executes `do_language`
directly and asserts tab titles and button text switch both ways.
Note: the API client (`gradio_client`) does not surface updates for
layout components (tabs), so tab switching is verified through the
raw handler and the frontend's reactive tab label — not through the
client.

### A-77 — `from __future__` must precede module-level constants (2026-11-26)

A latent Phase 8 break: `REMEMBER_ME_MAX_TOKENS = 3` sat between
the module docstring and `from __future__ import annotations` in
`logic/auth_service.py`, making the file uncompilable — no test had
imported it since. Fixed by moving the import to the top. Lesson:
any new module-level constant must go after all `__future__` imports.

### A-78 — The VM demo database is encrypted with the test-only key (2026-11-26)

The app database on the development VM (`data/wedding_hall.db`) was
upgraded to schema v5 during Phase 8 and its National IDs decrypt
with the test-only key in `tests/conftest.py` — it holds example
data only and is git-ignored, so fresh clones are unaffected but
must generate their own key before first launch (README "Encryption
key"). Do not re-key the VM database without recording the decision.
A fresh unused key was generated during the smoke test and was NOT
applied to the app database.

### A-79 — A-66 is superseded: FR-026 and FR-029 are built in Phase 8 (2026-11-26)

Ahmed approved building the remaining items on 2026-11-26 ("complete
the remaining Partial/Deferred tasks and add full Arabic"), so A-66's
"deferred at project end" no longer holds: FR-026 + BR-13, FR-029,
FR-011, FR-014 and NFR-005 are all Implemented + Verified in Phase 8
(34 new tests, `tests/test_phase8.py`). A-66 stays in this file as the
historical record of the Phase 7 position; the traceability matrix
carries the new status. The open instructor questions A-01/A-02/A-03
are unchanged — they are still unconfirmed.

### A-80 — Full Arabic: 1,295+ i18n keys, parity enforced by test (2026-11-26)

NFR-005 is complete: every visible string in all 24 tabs switches
with the language toggle, including all Phase 1–5 static form
labels, tab titles, buttons, messages, notifications, reports and
contract templates (Arabic `body_ar` mirrors the English template
body, same placeholders). The `LabelRegistry` (`ui/i18n.py`) builds
each component in Arabic by default and registers it once; the
toggle returns one merged `gr.update` per component (A-76). Frozen
price-breakdown labels are translated at display time by pattern;
report columns stay canonical English internally and are translated
at render time; CSV export keeps canonical English columns.
Acceptance is enforced by `tests/test_phase8.py`: every key exists
in both languages (set equality, 1,295 keys), all 24 tab titles are
Arabic and differ from English, and the toggle executes and switches
tab titles and button text both ways. Arabic stays the default;
English works fully when selected.

### A-81 — Notification de-duplication: UTC window + format normalization (2026-11-27)

Three tests failed only when the suite ran 00:00–02:00 Africa/Cairo
(`test_hold_expiring_notification_once`, `test_event_approaching_notification`,
`test_task_due_soon_and_overdue_notifications`): each second sweep created
one duplicate notification. Root cause was NOT Phase 8 — the bug was latent
since Phases 5–6 and only the wall-clock time exposed it. `exists_notification`
compared `notifications.created_at` (SQLite `CURRENT_TIMESTAMP`, UTC,
`"YYYY-MM-DD HH:MM:SS"` with a space) against `since_iso` built from naive
local time with `.isoformat()` (`"YYYY-MM-DDTHH:MM:SS"` with a `T`). As a
string comparison, `' ' (0x20) < 'T' (0x54)`, so whenever the UTC date of
`created_at` equalled the date of `since_iso` — every night 00:00–02:00 in
Africa/Cairo (UTC+2), when local `now − 24h` lands on the same calendar date
as UTC now — the check returned false and the warning was re-created. The
2h UTC/local skew (A-37) was not the cause; the format mismatch was. A-50's
and A-56's "~22h effective window" note described the skew only and missed
the breakage. Fix (two layers): (1) callers
(`BookingService._notify_time_based`, `TaskService.sweep_task_notifications`)
compute the dedup window from UTC now in `'YYYY-MM-DD HH:MM:SS'` format, so
the window is exactly 24h against `created_at`; the local `now` is still
used for the hold/event/due windows, which compare against naive-local
stored values. (2) `notification_repository.exists_notification` normalizes
both sides with SQLite `datetime()` so a `T`-separated value can never
break the comparison again. Verified: the 3 tests fail with
`TZ=Africa/Cairo` before the fix and pass after it, under both
`TZ=Africa/Cairo` and the default (UTC) timezone; full suite green under
both. The A-50/A-56 "~22h window" statements are superseded for the dedup
window (now exactly 24h); the lazy-sweep behaviour itself is unchanged.

### A-82 — Whole-page RTL: hidden direction signal + MutationObserver (2026-11-27)

Phase 6/8 made every string bilingual, but the page itself stayed
LTR: Gradio has no `rtl` flag, and the only direction in place was
`dir_wrap` around dynamic Markdown. The fix (in `ui/theme.py`,
wired in `ui/app.py`, applied in `__main__.py`): a hidden `gr.HTML`
component (`elem_id="whms-dir-signal"`, value
`<span data-lang="ar|en" data-dir="rtl|ltr">`) that every handler
which can change the language updates — login, remember-me restore,
the language toggle, logout (login outputs grew 10 → 11, and the
Phase 8 test asserting 10 was updated). The launch JavaScript
(`RTL_JS`) reads the signal and sets `document.documentElement.dir`
(and `lang`); a `MutationObserver` on the signal flips the direction
live on every toggle. Two Gradio-6 facts forced this shape:
(1) the app renders inside the `<gradio-app>` shadow DOM, so plain
`document.getElementById` cannot see the signal — the script goes
through `gradio-app.shadowRoot`, with a 300 ms polling fallback
(20 s cap) if the app is not mounted yet; (2) direction is an
inherited CSS property, so setting it on `<html>` flips every
Gradio component (flex layouts reverse automatically) — no
per-component RTL CSS is needed, and `[dir="rtl"]` selectors in the
custom CSS would NOT work because the CSS is injected inside the
shadow tree and cannot see the `<html>` attribute. The signal is
hidden with CSS (`#whms-dir-signal { display: none }`), not
`visible=False`, so the element is always in the DOM for the
observer. Verified live: served `/config` carries the JS (contains
`MutationObserver`) and the CSS; `gradio_client` login returns
`data-lang="ar"`, toggling to English returns `data-lang="en"` and
back to Arabic returns `data-lang="ar"` (`tests/test_ui_rtl_theme.py`).

### A-83 — Venue theme: Soft base, warm whites, petroleum/indigo/yellow (2026-11-27)

Gradio 6 moved `theme`/`css`/`js`/`head` from `gr.Blocks(...)` to
`launch()` — passing them to `Blocks` is a TypeError in 6.32, so
`ui/theme.py::launch_kwargs()` supplies them and `__main__.main`
passes `**launch_kwargs()` to `app.launch()`. Theme: `Soft` base,
`primary_hue="teal"` (petroleum), `secondary_hue="indigo"`,
`neutral_hue="stone"`, `text_size`/`spacing_size`/`radius_size`
`"lg"`, with `.set()` overrides — body `#FAF8F2` (warm
eye-comfortable white, never pure white; `#FFFFFF` is used nowhere
as a surface), blocks `#FFFDF8`, primary buttons petroleum `#0F766E`
(hover `#0D9488`), links/checkboxes petroleum, focus ring amber
`#D97706`, dark-mode twins (`#1C1917` body). Custom CSS (injected
into the shadow DOM, so it targets `.gradio-container`, not `body`):
a subtle dotted paper background
(`radial-gradient(rgba(15,118,110,0.10) 1px, transparent 1.3px)`,
26 px grid), a 4 px yellow top rule (`#EAB308`), the direction
signal hidden, the Gradio footer hidden. `background_fill_primary`
is `"transparent"` so the dots show over the body background (the
endorsed pattern from the gradio-themes skill). No new dependency;
Gradio's built-in theming + custom CSS only.

### A-84 — Fonts: system stack with Arabic coverage, no webfont download (2026-11-27)

The theme font is a system stack (`Segoe UI`, `Noto Kufi Arabic`,
`Tahoma`, `Arial`, `sans-serif`; mono `Cascadia Code`, `Consolas`,
`Courier New`, `monospace`) rather than a Google Font. A webfont
would need a runtime download on every load, which the venue LAN
deployment (offline by design, A-36) cannot depend on, and would
fail silently to a fallback anyway. The stack still reads as
distinctive rather than plain: `Noto Kufi Arabic`/`Cairo` render
Arabic in a Kufi style where installed, `Segoe UI`/`Tahoma` cover
the rest. If a nicer display font is wanted later, bundle a
`.woff2` locally and reference it via `head`/`css_paths` instead of
a CDN.

### A-85 — Revert the venue theme: back to Gradio default, footer + settings gear restored (2026-11-27)

Ahmed reviewed the polished UI on his own device and rejected it:
the new look was not nice, and the polish hid the "Built with
Gradio" footer and the settings gear he used to switch light/dark
mode. The whole theme is reverted: `ui/theme.py` is deleted
(`build_theme`, `APP_CSS`, the Soft base, petroleum/indigo/yellow,
dotted background, yellow top rule — all gone) and the app uses
Gradio's default theme again, so the footer and the light/dark
toggle come back. Lesson recorded in the `gradio-ui-rtl` skill:
never hide the Gradio footer with CSS — it is not decoration, it
carries the theme settings (light/dark) the user relies on. Any
future visual polish must keep the footer visible.

### A-86 — RTL via pure CSS `:has()` on the direction signal; JS demoted to secondary (2026-11-27)

The screenshot proved the JS-only mechanism did not work: the new
theme WAS running on Ahmed's device (teal buttons + yellow rule
visible) but direction stayed LTR, even though the served `/config`
contained `RTL_JS` and the toggle flipped the signal server-side.
The CSS injection channel demonstrably works (the yellow rule
rendered), so RTL now rides on CSS alone: `ui/rtl.py::RTL_CSS`
(passed to `launch(css=...)`) is exactly two rules —

```
.gradio-container:has(#whms-dir-signal [data-lang="ar"]) { direction: rtl; }
.gradio-container:has(#whms-dir-signal [data-lang="en"]) { direction: ltr; }
```

The hidden direction-signal component and every handler update to
it (login, remember-me restore, language toggle, logout) are
unchanged — that part was verified working. `RTL_JS` (the
`MutationObserver` through the `<gradio-app>` shadow DOM setting
`<html dir>`) stays only as a secondary belt-and-braces; the CSS
must carry the fix on its own. `ui/theme.py` is replaced by the
slim `ui/rtl.py` (signal HTML + the two-rule CSS + the JS, no theme
code), and `launch_kwargs()` now returns only `{"css", "js"}` — no
`theme` key. `tests/test_ui_rtl_theme.py` (7 tests) asserts the CSS
is exactly those two rules (no colors, background, or footer
rules) and the toggle flips the signal both ways. Verified by the
full suite + a live server check (served config carries the RTL
CSS, no theme). Remaining risk: the CSS flip is verified at
mechanism level, not by a screenshot — Ahmed does the visual check
on his device.
