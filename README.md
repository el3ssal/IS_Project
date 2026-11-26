# Wedding Hall Management System — IS201 Course Project

A management system for a wedding hall in Benha: customers, quotations,
bookings (with double-booking prevention), halls, packages, services,
contracts, payments, installments, expenses, refunds, staff tasks,
notifications, file management, search, dashboards, reports, a calendar,
manual backup/restore, and an Owner settings screen.

**Status: ALL 7 PHASES + PHASE 8 COMPLETE** (final phase, Phase 7,
finished 2026-11-26; follow-up Phase 8 — the remaining Partial/Deferred
items plus full Arabic — finished 2026-11-27). The app starts, the
database initialises at schema v5, all 5 demo accounts can log in, and
every feature is verified by tests (**351 unittest + 3 console tests,
all passing**). Final traceability: 68 of 68 functional requirements
implemented and verified, 16 of 16 non-functional requirements,
25 of 25 business rules enforced; 22 of 22 SRS §21 tests implemented
and verified; 12 of 12 deck requirements met.

Solo project by Ahmed Ashraf Elassal, Faculty of Computers and
Artificial Intelligence, Benha University. This is a fresh solo restart
(2026-11-26): no team, no shared scaffold. Phase 0 deliverables are in
`docs/` (traceability matrix, gap analysis, architecture review).

## Technology stack (approved)

- Python 3.12, OOP, 3-tier architecture (UI / logic / data in separate files)
- SQLite via the standard-library `sqlite3` module — no database server
- Gradio for the browser UI (the instructor deck's pattern: browser
  screens with `gr.Info` / `gr.Error`; see `docs/GAP_ANALYSIS.md` G-04)
- `requirements.txt` contains `gradio>=5,<7` and
  `cryptography>=50,<51` — no other frameworks. No React / Django /
  Flask / MongoDB / cloud. `cryptography` (Fernet, AES-128-CBC +
  HMAC-SHA256) is the vetted library used for field-level encryption
  of National IDs (FR-014, Phase 8); the key is never committed and
  never stored in the database — see "Encryption key" below.

## Quick start (Windows)

```powershell
git clone https://github.com/el3ssal/IS_Project.git
cd IS_Project
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/generate_encryption_key.py   # prints ONE key — save it
$env:WEDDING_HALL_ENCRYPTION_KEY = "<the key you just generated>"
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
`WEDDING_HALL_LANG`, `WEDDING_HALL_ENCRYPTION_KEY` (required — see
below), `WEDDING_HALL_BROWSER_SECRET` (optional — see "Remember Me"
below).

## Encryption key (FR-014, Phase 8)

Customer National IDs are encrypted at rest with Fernet
(`cryptography`). The key comes from the environment variable
`WEDDING_HALL_ENCRYPTION_KEY` — it is **never committed and never
stored in the database**. Generate one before the first run:

```text
python scripts/generate_encryption_key.py   # prints one key + setup instructions
```

- Windows (current shell): `$env:WEDDING_HALL_ENCRYPTION_KEY = "<key>"`
  (permanent: `setx WEDDING_HALL_ENCRYPTION_KEY "<key>"`, then restart
  the shell); Linux/macOS: `export WEDDING_HALL_ENCRYPTION_KEY="<key>"`.
- If the key is missing or invalid, the app **refuses to start the
  encryption path** with a clear message instead of silently storing
  plaintext (fail loudly, never fall back). Existing stored National
  IDs are encrypted in place by migration 005, which validates
  decrypt-after-encrypt per row.
- **Losing the key loses the National IDs** — back it up somewhere
  safe (password manager), separate from the database. Without it the
  data cannot be recovered.
- Phone and email are NOT encrypted (SRS §2.4 names the National ID);
  they stay protected by RBAC and masking, exactly as before.
- Note: the demo database on the development VM was encrypted with a
  test-only key; it holds example data only. A fresh clone must
  generate its own key before first launch.

**LAN deployment (SRS §16.4):** one central computer runs Python +
Gradio + SQLite; employee devices use a browser over the venue LAN.
`--lan` binds 0.0.0.0 and prints a warning that the app is exposed
on the local network — restrict it to the trusted LAN (host/firewall).
One shared database on the central host's local disk, never per-client
databases, never multiple app instances against separate copies.
Verified on this VM: loopback HTTP 200 in ~10 s, LAN mode HTTP 200 in
~6 s bound to 0.0.0.0.

## Features (by phase)

- **Phase 1 — Foundation:** 56-table schema (v5 after Phase 8),
  roles/permissions as data (5 roles, 42 permissions, least
  privilege), PBKDF2 auth with lockout (5 fails / 15 min), hashed
  session tokens, audit log, Owner/Manager/Receptionist/Accountant/
  Staff demo accounts.
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
- **Phase 8 — Remaining items + full Arabic:** Remember Me (FR-011,
  see below), National ID field-level encryption (FR-014, see above),
  post-confirmation service changes (FR-026: re-price, delta recorded,
  increase becomes outstanding with a due date, decrease creates a
  pending refund — never auto-paid — new contract version only when
  the total changes), scheduling advisory (FR-029: nearest feasible
  alternatives with a plain-language reason each, advisory only,
  never an automatic change), and full Arabic across the whole project
  (NFR-005: every label, tab title, button, message, notification,
  report and contract template switches with the language toggle;
  Arabic stays the default; 1,295+ i18n keys, identical in both
  languages — `tests/test_phase8.py` checks parity and that the
  toggle executes and switches tab titles and button text both ways).

## Remember Me (FR-011, Phase 8)

Login has a "Remember me on this device (30 days)" checkbox. When
checked, the server issues a random opaque token, stores only its
SHA-256 hash (30-day expiry, at most 3 live tokens per employee,
oldest revoked first), and hands it to the browser via Gradio
`BrowserState` (browser localStorage). On the next visit the token is
validated and **rotated** (a fresh token replaces it; the old one
dies), a new session is opened and the user is signed in. The token
is revoked on logout and on any password change/reset or account
deactivation. The password is never stored on the client. The
BrowserState secret is only obfuscation for localStorage — the real
security is server-side; set `WEDDING_HALL_BROWSER_SECRET` to a
stable secret if you want "Remember Me" to survive a server restart
(without it, Gradio generates a fresh secret per process and a restart
simply asks the user to log in again). Without Remember Me, sessions
still last up to 8 h as before.

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
python -m unittest discover -s tests      # full suite → 351 tests, OK
```

Every test uses a temporary database (never the real app database).
Test files: `test_auth.py`, `test_permissions.py`, `test_database.py`,
`test_halls.py`, `test_pricing.py`, `test_customers.py`,
`test_quotations.py`, `test_bookings.py`, `test_contracts.py`,
`test_tasks.py`, `test_notifications.py`, `test_finance.py`,
`test_files_search_reports.py`, `test_i18n.py`, `test_backup.py`,
`test_settings.py`, `test_calendar.py`, `test_phase8.py`, plus
`test_console.py`.
Test counts by phase: 34 + 44 + 66 + 35 + 51 + 47 + 40 + 34 = 351.

**2-minute demo:** `docs/DEMO_SCRIPT.md` — a timed script (Owner login
→ dashboard → price calculator → quotation accept + convert → deposit
→ contract → event plan/task → finance/reconciliation → CSV export)
verified against the seeded app database. Reset before a fresh demo:
delete `data/wedding_hall.db` (and `-wal`/`-shm`) and restart to
re-seed, or restore a pre-demo backup.

## Project structure

```text
main.py                    Thin launcher: python main.py [--lan] [--host] [--port]
requirements.txt           gradio + cryptography (stdlib otherwise)
wedding_hall_system/
  __main__.py              CLI + offline env vars (GRADIO_ANALYTICS_ENABLED=False, HF_HUB_OFFLINE=1)
  config/                  settings.py (AppSettings, env overrides), defaults.py (seed data)
  data/                    database.py (DatabaseManager), schema.sql, seed.py,
                           migrations/ (001–005), migrations_py.py (Python migration steps),
                           repositories/ (one per aggregate)
  logic/                   models.py, permissions.py, auth_service.py,
                           audit_service.py, hall_service.py,
                           catalog_service.py, availability_service.py, pricing_service.py,
                           customer_service.py, quotation_service.py, booking_service.py,
                           contract_service.py, task_service.py, staff_service.py,
                           notification_service.py, payment_service.py, expense_service.py,
                           refund_service.py, report_service.py, search_service.py,
                           attachment_service.py, calendar_service.py, backup_service.py,
                           settings_service.py, preferences.py, strings.py
  ui/                      app.py, auth_views.py, catalog_views.py, booking_views.py,
                           operations_views.py, finance_views.py, dashboard_views.py,
                           search_views.py, files_views.py, reports_views.py,
                           calendar_views.py, backup_views.py, settings_views.py,
                           i18n.py, components/
  utils/                   exceptions.py (13 custom exception types incl. ConfigurationError),
                           logging_config.py (secret redaction), validators.py,
                           crypto.py (Fernet field-level encryption)
storage/                   contracts/, uploads/, backups/ (contents git-ignored)
data/                      wedding_hall.db (created on first run; schema v5)
tests/                     conftest.py, test_console.py, test_*.py (see Tests above)
scripts/                   perf_check.py, generate_encryption_key.py
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
- **Hall utilization is an upper bound** (A-54): it counts weekly-
  schedule hours only; exceptional closures are not subtracted.
- **Scheduling advisory is advisory only** (A-73): it suggests
  alternatives and never changes a booking; shorter-duration
  suggestions never go below the package minimum.
- **Service changes need a Confirmed booking** (A-69): an in-progress
  event is being delivered, not re-priced.
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
  FR 68/68 implemented and verified, NFR 16/16, BR 25/25, T 22/22
- `docs/ARCHITECTURE_REVIEW.md`, `docs/GAP_ANALYSIS.md`,
  `docs/ASSUMPTIONS_AND_DECISIONS.md` (A-01–A-80)
- `docs/DEMO_SCRIPT.md`, `docs/PERFORMANCE.md`, `docs/diagrams/`
