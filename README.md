# Wedding Hall Management System — IS201 Course Project

A management system for a wedding hall in Benha: customers, quotations,
bookings (with double-booking prevention), halls, packages, services,
contracts, payments, installments, expenses, refunds, staff tasks,
notifications, file management, search, dashboards, reports, a calendar,
manual backup/restore, and an Owner settings screen.

**Status: ALL 7 PHASES COMPLETE** (final phase, Phase 7, finished
2026-11-26). The app starts, the database initialises at schema v4,
all 5 demo accounts can log in, and every feature is verified by tests
(**317 unittest + 3 console tests, all passing**). Final traceability:
64 of 68 functional requirements implemented and verified, 2 Partial
(documented limitations the SRS itself anticipates), 2 Deferred
(FR-026 post-confirmation service changes, FR-029 scheduling advisory);
22 of 22 SRS §21 tests implemented and verified; 12 of 12 deck
requirements met.

Solo project by Ahmed Ashraf Elassal, Faculty of Computers and
Artificial Intelligence, Benha University. This is a fresh solo restart
(2026-11-26): no team, no shared scaffold. Phase 0 deliverables are in
`docs/` (traceability matrix, gap analysis, architecture review).

## Technology stack (approved)

- Python 3.12, OOP, 3-tier architecture (UI / logic / data in separate files)
- SQLite via the standard-library `sqlite3` module — no database server
- Gradio for the browser UI (the instructor deck's pattern: browser
  screens with `gr.Info` / `gr.Error`; see `docs/GAP_ANALYSIS.md` G-04)
- `requirements.txt` contains only `gradio>=5,<7` — no other frameworks.
  No React / Django / Flask / MongoDB / cloud.

## Quick start (Windows)

```powershell
git clone https://github.com/el3ssal/IS_Project.git
cd IS_Project
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Then open **http://127.0.0.1:7860** in a browser and log in as Owner
(see demo accounts below). The database `data/wedding_hall.db` is
created and seeded on first run. On Linux/macOS the same steps apply
with `python3 -m venv .venv` and `source .venv/bin/activate`.

## Demo accounts (DEMO ONLY — change before real use)

| Username | Password | Role |
|---|---|---|
| `owner` | `Owner123!` | Owner |
| `manager` | `Manager123!` | Manager |
| `receptionist` | `Reception123!` | Receptionist |
| `accountant` | `Account123!` | Accountant |
| `staff` | `Staff123!` | Staff |

These passwords are seeded examples. Change them (Change Password
screen, or an authorized password reset by the Owner) before any real
use — the system never stores a password in plaintext (PBKDF2-HMAC-SHA256,
unique salt, 600,000 iterations).

## Running the app

```text
python main.py                 # loopback only: http://127.0.0.1:7860
python main.py --lan           # LAN mode: binds 0.0.0.0 (see below)
python main.py --host 0.0.0.0 --port 7866
```

Environment variables: `GRADIO_SERVER_NAME` (default `127.0.0.1`),
`GRADIO_SERVER_PORT` (default `7860`), `WEDDING_HALL_DB`,
`WEDDING_HALL_VENUE`, `WEDDING_HALL_CURRENCY`, `WEDDING_HALL_TZ`,
`WEDDING_HALL_LANG`.

**LAN deployment (SRS §16.4):** one central computer runs Python +
Gradio + SQLite; employee devices use a browser over the venue LAN.
`--lan` binds 0.0.0.0 and prints a warning that the app is exposed
on the local network — restrict it to the trusted LAN (host/firewall).
One shared database on the central host's local disk, never per-client
databases, never multiple app instances against separate copies.
Verified on this VM: loopback HTTP 200 in ~10 s, LAN mode HTTP 200 in
~6 s bound to 0.0.0.0.

## Features (by phase)

- **Phase 1 — Foundation:** 54-table schema (v4), roles/permissions as
  data (5 roles, 41 permissions, least privilege), PBKDF2 auth with
  lockout (5 fails / 15 min), hashed session tokens, audit log,
  Owner/Manager/Receptionist/Accountant/Staff demo accounts.
- **Phase 2 — Halls, catalog, pricing:** hall CRUD (deactivate, never
  hard delete), weekly hours (multi-interval) + exceptional hours/
  closures, maintenance blocks, event types, packages, services,
  pricing rules, dynamic pricing with itemised breakdown (package-
  included services at 0, never double-charged; manual discount capped
  at 20%), price/policy snapshots frozen at confirmation.
- **Phase 3 — Customers, quotations, bookings:** duplicate detection
  (normalized phone/National ID/email), quotations (Draft/Sent/
  Accepted/Expired, price frozen, availability checked but time NOT
  reserved), bookings (Pending → Confirmed → In Progress → Completed,
  plus Cancelled/No-show/Expired), 48 h hold + 24 h grace, 30%
  deposit as a real payment row (partial deposits accumulate, reaching
  the threshold auto-confirms). **Double-booking prevention:** creation
  runs in one `BEGIN IMMEDIATE` transaction re-checking availability
  on the same connection; proven by a 2-thread concurrency test.
- **Phase 4 — Contracts, operations:** versioned HTML contracts
  (printable to PDF from the browser; no PDF library — see A-38),
  signed-copy upload, event plans + task templates, task assignment
  with warn-only staff conflicts, staff profiles/availability, in-app
  notifications.
- **Phase 5 — Finance:** payments (Cash, Bank Transfer, Visa/Card,
  InstaPay, Other) with full-amount reversals (original never edited),
  installments (one plan per Confirmed booking, even split, lazy
  overdue, late fee computed-only, never auto-charged), expenses +
  approval, refunds with dual caps (per-payment + per-booking pool),
  tiered cancellation fees from the confirmation-time snapshot,
  rescheduling (atomic re-check + re-price), no-show (deposit
  forfeited), ownership transfer, rebooking links, cash-basis
  reconciliation (0 discrepancies in smoke test).
- **Phase 6 — Files, search, dashboards, reports, bilingual UI:**
  protected file management (PDF/JPG/PNG, 10 MB, magic-byte check,
  authorized-only downloads), permission-filtered advanced + global
  search (National ID always masked), role dashboards + pending-
  booking dashboard, 8 reports with Gradio BarPlot charts and CSV
  export for every report, audit search screen, Arabic/English UI
  with RTL/LTR (Arabic default).
- **Phase 7 — Backup, deployment, final documentation:** Owner-only
  manual backup/restore with validation (see below), LAN deployment
  config, performance checks (`docs/PERFORMANCE.md`), final UML + DFD
  diagrams (`docs/diagrams/`), README/SRS/traceability updates,
  2-minute demo script (`docs/DEMO_SCRIPT.md`), Owner settings screen,
  calendar (see below).

## Backup and restore (Owner only, `backup.manage`)

Backup & Restore tab (FR-068): create a backup, list backups, validate,
download, restore. A backup uses SQLite `Connection.backup` (never a
blind copy of a live DB file) and is stored with a SHA-256 checksum,
schema version and a manifest JSON sidecar that inventories the files
under `storage/`. Restore validates first (`PRAGMA integrity_check`,
checksum match, schema version match) and **refuses** a corrupt or
wrong-version backup; before replacing the live database it makes a
safety copy (`safety_before_restore_<timestamp>.db`), which is recorded
in the restored database. Backup/restore is audited.

**Limitation (A-62): the backup covers the database only.** Uploaded
file and contract document bytes are inventoried in the manifest, not
copied. Back up `storage/` separately if you need the file bytes.

## Calendar (FR-057/FR-058)

Calendar tab: Day/Week/Month views of confirmed bookings, active
Pending holds, maintenance blocks and closures/special hours, for all
halls or one hall, with a detail panel (money fields permission-gated).
**No drag-and-drop** (Gradio has none, GAP G-13): moving a booking
from the calendar uses a date/time edit that runs the SAME
rescheduling workflow as the Bookings screen — full revalidation,
permissions and price recalculation, never a direct move (A-61).

## Owner settings (FR-067)

Settings tab (Owner only, `settings.manage`): view/edit the 20 system
settings — venue name/contact, currency, default language, timezone,
hold/grace/buffer/minimum-duration, deposit %, pricing caps,
notification config, dashboard widgets, attachment limits,
cancellation/no-show policy — with per-spec validation and an audit
row (old/new) for every change. Changes affect future transactions
only; confirmed prices and policy snapshots are never rewritten.

## Performance (measured, `docs/PERFORMANCE.md`)

15 measurements on the seeded demo database and a generated
200-booking fixture; **0 over target**. Examples: login 0.064 s /
0.028 s, price calculation 0.008 s, dashboard 0.035 s / 0.041 s,
global search 0.075 s / 0.037 s (target ≤ 3 s), booking report
0.004 s / 0.153 s, financial report 0.017 s / **1.608 s** (target
≤ 2 s — closest to target; it recomputes per-booking reconciliation
rows), booking create + deposit 0.089 s / 0.061 s. Gradio cold start
to HTTP 200 is ~9 s on this machine. Targets are not guarantees.
Re-run with `python scripts/perf_check.py` (uses a temporary
database; the demo database is never modified).

## Tests

```powershell
python tests/test_console.py              # deck's 3 console cases incl. bad input → 3/3 PASS
python -m unittest discover -s tests      # full suite → 317 tests, OK
```

Every test uses a temporary database (never the real app database).
Test files: `test_auth.py`, `test_permissions.py`, `test_database.py`,
`test_halls.py`, `test_pricing.py`, `test_customers.py`,
`test_quotations.py`, `test_bookings.py`, `test_contracts.py`,
`test_tasks.py`, `test_notifications.py`, `test_finance.py`,
`test_files_search_reports.py`, `test_i18n.py`, `test_backup.py`,
`test_settings.py`, `test_calendar.py`, plus `test_console.py`.
Test counts by phase: 34 + 44 + 66 + 35 + 51 + 47 + 40 = 317.

**2-minute demo:** `docs/DEMO_SCRIPT.md` — a timed script (Owner login
→ dashboard → price calculator → quotation accept + convert → deposit
→ contract → event plan/task → finance/reconciliation → CSV export)
verified against the seeded app database. Reset before a fresh demo:
delete `data/wedding_hall.db` (and `-wal`/`-shm`) and restart to
re-seed, or restore a pre-demo backup.

## Project structure

```text
main.py                    Thin launcher: python main.py [--lan] [--host] [--port]
requirements.txt           gradio only (stdlib otherwise)
wedding_hall_system/
  __main__.py              CLI + offline env vars (GRADIO_ANALYTICS_ENABLED=False, HF_HUB_OFFLINE=1)
  config/                  settings.py (AppSettings, env overrides), defaults.py (seed data)
  data/                    database.py (DatabaseManager), schema.sql, seed.py,
                           migrations/ (001–004), repositories/ (one per aggregate)
  logic/                   models.py, permissions.py, auth_service.py,
                           audit_service.py, hall_service.py,
                           catalog_service.py, availability_service.py, pricing_service.py,
                           customer_service.py, quotation_service.py, booking_service.py,
                           contract_service.py, task_service.py, staff_service.py,
                           notification_service.py, payment_service.py, expense_service.py,
                           refund_service.py, report_service.py, search_service.py,
                           attachment_service.py, calendar_service.py, backup_service.py,
                           settings_service.py, preferences.py
  ui/                      app.py, auth_views.py, catalog_views.py, booking_views.py,
                           operations_views.py, finance_views.py, dashboard_views.py,
                           search_views.py, files_views.py, reports_views.py,
                           calendar_views.py, backup_views.py, settings_views.py,
                           i18n.py, components/
  utils/                   exceptions.py (12 custom exception types),
                           logging_config.py (secret redaction), validators.py
storage/                   contracts/, uploads/, backups/ (contents git-ignored)
data/                      wedding_hall.db (created on first run; schema v4)
tests/                     conftest.py, test_console.py, test_*.py (see Tests above)
scripts/                   perf_check.py
docs/                      REQUIREMENT_TRACEABILITY_MATRIX.md, GAP_ANALYSIS.md,
                           ARCHITECTURE_REVIEW.md, ASSUMPTIONS_AND_DECISIONS.md,
                           SRS.md (as-built), DEMO_SCRIPT.md, PERFORMANCE.md,
                           diagrams/ (Use Case, Activity, Sequence, DFD,
                           Class + ER appendices; .mmd + .png + .svg)
```

## Seed data — EXAMPLES, not real hall data

Halls, packages, services, pricing rules, customers, the quotation and
the contract template are clearly-marked **examples** ("Main Hall
(example)", 400 guests / 15,000 EGP; "Classic Wedding (example)",
25,000 EGP; Friday/Saturday +10%). Real hall data is in neither source
document — the Owner must replace the seeds with the real venue data
before real use. Cancellation tiers (≥30 days 10%, 14–29 days 25%,
<14 days 50%, capped at amount paid) and the no-show rule (deposit
forfeited) are seed defaults (A-43/A-44); the Owner must confirm them
with the hall.

## Known limitations

- **Contracts are versioned HTML, not PDF** (A-38): instructor
  permission for a PDF library (open question A-03) is still open, so
  no PDF library was installed. Contracts print to PDF from the browser.
  No PDF/Excel report export either (CSV is the mandatory academic export).
- **Backup covers the database only** (A-62) — see above.
- **Calendar is a table view, not drag-and-drop** (A-61) — see above.
- **Bilingual UI is partial** (A-55): Arabic/English with RTL/LTR
  covers the Phase 6 screens, the login/welcome chrome and all dynamic
  content; the static form labels of the Phase 1–5 screens stay English.
- **Hall utilization is an upper bound** (A-54): it counts weekly-
  schedule hours only; exceptional closures are not subtracted.
- **No "Remember Me"** (A-09) and **no field-level encryption** (A-08):
  both are documented limitations the SRS anticipates (omit + document);
  sessions last up to 8 h, and sensitive data is protected by RBAC,
  masking and never logging secrets.
- **Deferred, not built:** FR-026 post-confirmation service changes
  and FR-029 scheduling advisory (A-66) — pending a scope decision.
- Hold expiry and the task-due sweep are **lazy** (no background
  process): they run when the relevant screen is opened.
- Datetimes are stored as naive local ISO strings in venue time
  (Africa/Cairo); never mix them with UTC timestamps (A-37).

## Open instructor questions (unconfirmed)

- **A-01** — Is a wedding hall accepted as a custom topic? The
  instructor deck lists 16 ideas; the wedding hall is not one of them
  (closest analogue: Idea 03 Hotel Booking Desk).
- **A-02** — Submission date, format, grading weights. Not in the deck.
- **A-03** — PDF library permission (see contracts above).

These are outside the implementers' control; they are recorded, not
assumed. The goal tracker due date 2026-12-01 is **not** a deadline
stated in the deck — it coincides with an old GitHub invite expiry.

## Documentation

- `docs/SRS.md` — as-built software requirements (sections 0–26)
- `docs/REQUIREMENT_TRACEABILITY_MATRIX.md` — final counts: AC 12/12,
  FR 64 implemented + 2 Partial + 2 Deferred, NFR 15 + 1 Partial,
  BR 24 enforced + 1 Deferred, T 22/22
- `docs/ARCHITECTURE_REVIEW.md`, `docs/GAP_ANALYSIS.md`,
  `docs/ASSUMPTIONS_AND_DECISIONS.md` (A-01–A-67)
- `docs/DEMO_SCRIPT.md`, `docs/PERFORMANCE.md`, `docs/diagrams/`
