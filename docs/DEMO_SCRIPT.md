# 2-Minute Demo Script — Wedding Hall Management System

**Delivery requirement:** instructor deck slide 2, row "Delivery" —
code, README, 4 diagrams and a 2-minute demo. This script is timed
to ~2 minutes and uses only the seeded demo data, so it runs on a
fresh database. Every step below was verified end-to-end in Phase 7
(see "Verification" at the end).

**Setup before the demo:** `python main.py`, open
http://127.0.0.1:7860. Log in as **owner / Owner123!** (demo account).

**Note:** the demo creates real rows (a booking, a deposit payment,
a contract, a task assignment). To run it again from a clean state,
stop the app, delete `data/wedding_hall.db` (and
`data/wedding_hall.db-wal` / `-shm` if present), and restart — the
database is re-created and re-seeded. Or restore the pre-demo backup
from the Backup & Restore tab.

## Script (2:00)

### 0:00–0:10 — Login + Dashboard
Log in as `owner` / `Owner123!`. The welcome panel shows the Owner's
roles and permissions. Open the **Dashboard** tab: revenue, bookings,
pending-bookings and refunds widgets for the Owner role.
*Say: "One login, role-based dashboards — the Owner sees revenue and
profit, Staff sees only their own tasks."*

### 0:10–0:30 — Price calculator (Halls & Pricing)
Open **Halls & Pricing → Price calculator**: hall "Main Hall
(example)", package "Classic Wedding (example)", event type Wedding,
next Friday, 18:00–23:00, 200 guests. The itemised breakdown shows
hall 15,000 + package 25,000 + 10% Friday surcharge = **44,000 EGP**,
and availability says the slot is free.
*Say: "Dynamic pricing with a breakdown, and the same check that
blocks double-bookings."*

### 0:30–0:55 — Quotation → booking → deposit (Customers / Bookings)
Open **Customers / Quotations / Bookings → Quotations**: the seeded
Draft quotation for "Ahmed Hassan (example)" (Main Hall, Classic
Wedding, next Friday 18:00–23:00). Click **"Accept & convert to
booking"** — it becomes a Pending booking with a 48-hour hold. Then
record the deposit: **13,200 EGP (30% of 44,000)** in cash. The
booking becomes **Confirmed** automatically once the deposit is
verified.
*Say: "A quotation never reserves the hall; the deposit confirms the
booking — no separate approval step."*

### 0:55–1:15 — Contract (Contracts)
Open **Contracts**: generate the contract for the Confirmed booking.
A versioned contract `CT-YYYYMMDD-0001` is created (HTML, printable
to PDF from the browser) with the payment and cancellation terms
frozen in. Regenerating creates version 2; version 1 stays readable.
*Say: "Contracts are versioned — editing a signed contract never
rewrites history."*

### 1:15–1:35 — Event plan + task (Event Plans & Tasks)
Open **Event Plans & Tasks**: the Confirmed booking already has an
event plan generated from the "Wedding Day Checklist (example)"
template (4 tasks). Assign "Confirm hall setup and seating" to the
staff account — no conflict warning appears. Switch to the staff
login later and the task is theirs to update.
*Say: "The plan is generated from templates; assignment warns about
conflicts instead of blocking."*

### 1:35–1:50 — Finance (Finance)
Open **Finance**: the installment plan (3 installments over the
remaining 30,800 EGP) and the reconciliation summary — contract
44,000 EGP, collected 13,200 EGP, outstanding 30,800 EGP,
0 discrepancies.
*Say: "Cash-basis reconciliation — every figure is computed from
real payments, never typed in."*

### 1:50–2:00 — Report + CSV export (Reports)
Open **Reports → Booking report**: the new booking is listed.
**Export CSV** and open the file — headers + rows, money as decimal
EGP. End on the report.
*Say: "Eight reports, every one exportable to CSV — the mandatory
academic export."*

## Verification (Phase 7, run 2026-11-26)

The script was verified against a temporary database (schema v4) by
running the same steps through the service layer plus live server
checks (loopback + `--lan` mode). Exact run output:

- App serves HTTP 200 in 10.1 s (loopback, cold start); all 5 demo
  logins work; schema v4.
- Friday Classic Wedding, 200 guests, 18:00–23:00 → 44,000 EGP
  (hall 15,000 + package 25,000 + 10% Friday rule).
- Example quotation → accept → convert → Pending booking
  `BK-20261126-0001` → deposit 13,200 EGP → Confirmed; hold 48.0 h.
- Contract `CT-20261126-0001` version 1 generated from the Confirmed
  booking; regeneration creates version 2, version 1 preserved.
- Event plan generated (4 tasks from the example template); task
  assigned to staff with no warnings; staff marks it in progress.
- Installment plan (3 installments over the remaining balance);
  reconciliation shows contract 44,000 EGP, collected 13,200 EGP,
  outstanding 30,800 EGP, 0 discrepancies.
- Bookings CSV export contains `BK-20261126-0001`.
- Calendar shows the booking on the event day; backup created and
  validated; settings update works.
- LAN mode (`python main.py --lan`): HTTP 200 in 6.3 s, bound to
  0.0.0.0:7866.

Smoke-test database and files were removed afterwards; the real app
database was not touched by the smoke run.
