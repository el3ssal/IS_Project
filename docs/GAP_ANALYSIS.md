# Gap and Conflict Analysis — Wedding Hall Management System

**Project:** Wedding Hall Management System, Introduction to IS course project (solo, Ahmed Ashraf Elassal)
**Phase:** 0 — Requirements analysis (fresh restart, solo)
**Date:** 2026-11-26
**Sources:** instructor deck `Final_Project_Ideas_Full.pptx` (135 slides) and SRS v1.0. Every deck claim was verified against the extracted slide text; every SRS claim against the SRS section cited.

**Severity key:** 🔴 blocks academic compliance until answered · 🟡 design decision needed, work can proceed with a documented choice · 🟢 noted, no action needed.

## 1. Conflicts between the deck and the SRS

### G-01 — A wedding hall is not one of the instructor's 16 ideas 🔴

- **Fact.** The deck's catalog (slides 5–6) lists 16 ideas; the words "wedding" and "hall" occur zero times in all 135 slides. Slide 135 says: "Pick one idea, then follow its Roadmap slide. Each project must meet the requirements on slide 2." No slide offers a custom-topic option.
- **Closest analogue.** Idea 03, Hotel Booking Desk (slides 23–30): rooms → halls, reservations → bookings, signature rule "No overlapping dates for the same room", pricing by nights with a 10% discount after 5 nights. The SRS adapts this pattern to halls with start/end datetimes, buffers, holds and deposits.
- **Why it matters.** If the instructor only accepts the 16 listed ideas, the whole project fails the topic test regardless of how well it is built. The SRS itself admits this risk: §0.3 states the deck "was not available to inspect while preparing this document" and "where the instructor's brief is stricter than this document, the instructor's brief wins".
- **Resolution proposed.** Ask the instructor explicitly whether a wedding hall is accepted as a custom topic, presenting the Hotel Booking Desk mapping as justification. Do not assume silence means yes. If refused, the fallback is Idea 03 (Hotel Booking Desk) reusing the same architecture, schema pattern and services with rooms instead of halls.

### G-02 — Diagram count and types: deck says 4, SRS wants 7+ 🟡

- **Fact.** Deck slide 2 (Delivery row): "Code, README, 4 diagrams and a 2-minute demo". Slide 4 names the four types: Use Case, Activity, Sequence, Data Flow.
- **Conflict.** SRS §23 lists Use Case, Class, ER, Activity/Sequence, DFD Context, DFD Level 0, and DFD Level 1 — at least 7 diagrams, including two types (Class, ER) the deck never asks for.
- **Resolution proposed.** Deliver the deck's 4 as the graded set (Use Case, Activity, Sequence, DFD with context + Level 0; Level 1 for booking/finance only if it does not inflate the count past what the deck asks). Keep Class and ER diagrams as clearly labelled appendices that support the code and schema but are not counted as part of the required 4. Ask the instructor whether extra diagrams are welcome or counted against the delivery.

### G-03 — Testing form: "3 console test cases" (deck) vs "unittest, 22 tests" (SRS) 🟡

- **Fact.** Deck slide 2 (Testing row): "3 console test cases including bad input"; slide 3 step 6 output is `test_console.py`. SRS §21 requires Python standard `unittest` and lists 22 named tests (T-01–T-22), noting "at least three tests are academically required, but implement the meaningful suite above".
- **Conflict.** None in substance — the SRS suite contains the deck's requirement — but the *form* differs: the deck grades a console script, the SRS grades a `unittest` suite.
- **Resolution proposed.** Ship both: `tests/test_console.py` with exactly 3 console cases (including bad input) as the deck's deliverable, plus the full `unittest` suite covering SRS §21. The console script can call the same service code the unit tests cover, so there is one implementation and two test harnesses.

### G-04 — Gradio is implied by the deck, never stated as a requirement row 🟡

- **Fact.** Slide 2 has no "Gradio" or "UI technology" row. "Gradio" appears only inside the sequence diagrams ("User / UI (Gradio) / Service / Database", e.g. slides 13, 21, 29) and in roadmap step 5, "Every screen works in browser". The error row on slide 2 names `gr.Info` / `gr.Error` notifications, which are Gradio APIs.
- **Assessment.** The deck's diagrams assume a Gradio browser UI, and the SRS (§0.1, §17) names Gradio explicitly. Adopting Gradio is safe and consistent, but strictly speaking the deck never *requires* it by name. No other UI technology is named anywhere in the deck (Tkinter appears zero times).
- **Resolution proposed.** Build the UI in Gradio; note in the README that the deck's sequence diagrams prescribe Gradio and the SRS confirms it.

### G-05 — "Python" is not a named requirement row on slide 2 🟢

- **Fact.** The word "Python" occurs twice in the deck: slide 3 step 3 cites "W9 sqlite3", and one idea roadmap mentions storing data "as a Python dict". Slide 1 calls the project a "3-tier capstone: UI + OOP logic + SQLite". No slide-2 row names Python.
- **Assessment.** Not a real risk: `sqlite3` is a Python standard-library module, the SRS (§0.1) names Python explicitly, and the course is evidently Python-based. Recorded here only so nobody claims the deck mandates Python by name.

### G-06 — The deck contains no deadline, rubric, submission format, or team rules 🔴

- **Fact.** A full-text search of all 135 slides finds no submission date, no grading weights, no rubric, no submission format (zip? repo link? demo format?), and no team-size rule. The only delivery statement is slide 2: "Code, README, 4 diagrams and a 2-minute demo".
- **Why it matters.** The project cannot be declared "submitted correctly" without these. The SRS §0.3 list of deliverables was written without the deck and must be re-checked against whatever the instructor publishes.
- **Resolution proposed.** Ask the instructor for: submission date, submission format, grading weights, and whether a GitHub link is an acceptable submission. Until then, assume the slide-2 delivery (code + README + 4 diagrams + 2-minute demo). Work is kept locally under `files/is-project/` in Phase 0; the GitHub repo `el3ssal/IS_Project` was deleted by Ahmed on 2026-11-26 and will be recreated fresh when there is code to push.
- **Solo-project note.** This restart is solo (Ahmed alone, his own account). No team rules are needed; the absence of team rules in the deck is therefore not a problem for this project.

## 2. Scope gaps — the SRS is much larger than the instructor minimum

### G-07 — Scope explosion risk 🟡

- **Fact.** The deck's minimum is: 3+ tables, 3+ classes, 2+ business rules, CRUD + search, 1 CSV report, 3 console tests. The SRS specifies ~48 database entities, 68 functional requirements, 25 business rules, 22 tests, 11 use cases, bilingual UI, file management, audit, notifications, dashboards, backup/restore and contracts.
- **Risk.** A solo student building the full SRS in one course term is the single biggest delivery risk. The deck's minimum could be met in a week; the SRS is a multi-month system.
- **Mitigation.** Phases are ordered so that academic compliance is reached early: Phase 1 (foundation) + Phase 2 (halls/pricing) + Phase 3 (customers/quotations/bookings) already exceed every deck row. Phases 4–7 add SRS depth. If time runs short, Phases 6–7 can be trimmed to the deck minimum (CSV export, bilingual core screens, backup, diagrams, demo) without breaking compliance — but any trimming must be a conscious, documented decision, never silent scope loss.

### G-08 — Contract PDF export needs a dependency the deck never approves 🟡

- **Fact.** SRS §6 requires PDF export of contracts and says: "If a PDF library is not permitted by course requirements, provide a clear alternative and document it; do not silently introduce a major dependency." The deck approves no PDF library; its only export requirement is CSV (slide 2).
- **Resolution proposed.** Ask the instructor whether a PDF library (e.g. `reportlab` or `fpdf2`) is permitted. Fallback if not: generate the contract as a printable HTML page in Gradio plus a CSV/text record, and document that PDF is unavailable under the approved stack. Never add the dependency silently.

### G-09 — Field-level encryption: do not fake it 🟡

- **Fact.** SRS §2.4 requires encryption of particularly sensitive stored values "where feasible", but only with "a vetted available cryptographic dependency only after confirming course permission", keys outside the database, and documented key backup/recovery. It explicitly allows documenting the limitation and applying strict access controls instead.
- **Resolution proposed.** Decide in Phase 1: either (a) confirm a permitted dependency and implement real encryption with keys outside the DB, or (b) record the limitation (no field encryption in v1; RBAC + masking + redacted logging instead). Never claim encryption that does not exist.

### G-10 — "Remember Me" / persistent sessions in Gradio 🟢

- **Fact.** SRS §2.3 requires Remember Me to use a secure session mechanism and says to omit it and document the limitation if it cannot be implemented safely with the selected Gradio version and deployment mode.
- **Resolution proposed.** Phase 1 implements in-memory (per-process) sessions with hashed tokens and expiry. Persistent Remember Me is deferred; if it cannot be done safely, it is omitted and the limitation is documented. This is compliant by the SRS's own terms.

### G-11 — Hold expiry needs a trigger, and nothing in the stack runs in the background 🟡

- **Fact.** SRS §3.4 requires a Pending booking to become Expired when hold + grace period pass without a valid deposit. The approved stack (Python + Gradio + SQLite) has no scheduler, cron, or background worker; Gradio serves requests only.
- **Resolution proposed.** Expire holds lazily: every availability check, booking read, and dashboard load first runs an "expire overdue holds" sweep inside the same transaction, and the Pending dashboard shows a countdown. This is deterministic, testable (T-07), and needs no background process. Document the choice: expiry is processed on access, not at the exact second the hold ends.

## 3. Technical risks

### G-12 — SQLite concurrency and double-booking prevention 🔴 (highest technical risk)

- **Fact.** SQLite has no exclusion constraint for overlapping time ranges (SRS §18.10 states this explicitly). Two concurrent requests can both pass a read-only availability check and then both insert.
- **Required design (SRS §3.2).** Validate and insert inside one write transaction with `BEGIN IMMEDIATE`; re-check availability immediately before commit; index hall/date/status queries; handle lock/busy with a friendly retry/error message; never rely on a UI-side check.
- **Residual risk.** SQLite allows one writer at a time; under LAN use (6–10 employees, SRS §16.1) a second writer waits on `busy_timeout` and may still fail. The design must treat "database is locked" as a normal, friendly error, not a crash. A race test (two threads attempting the same slot) is required in Phase 3.
- **Deployment constraint.** The DB must live on the central host's local disk, never on a network share (SRS §16.2); WAL mode only after validating local-filesystem assumptions.

### G-13 — Gradio calendar: no native drag-and-drop 🟡

- **Fact.** SRS §9.2 requires drag-and-drop only to *start* the rescheduling workflow, never to mutate a booking directly, and §25.14 admits true drag-and-drop may not be realistically supported without another framework.
- **Resolution proposed.** Implement calendar views as Gradio dataframes/tables with a date/time edit action that starts the same rescheduling workflow (same validation, same approval). Document that native drag-and-drop is not available in the approved stack. This satisfies the SRS's own fallback.

### G-14 — Arabic RTL in Gradio 🟡

- **Fact.** SRS §16.3 requires bilingual Arabic/English with RTL/LTR, per-user language, and translation of navigation, buttons, messages, notifications, reports and contract templates. Gradio's built-in RTL support is limited: component direction, table alignment and mixed Arabic/English text need explicit handling (custom CSS, `dir="rtl"` wrappers, mirrored layout).
- **Risk.** RTL bugs are cosmetic but visible to the grader; a half-translated UI looks unfinished.
- **Mitigation.** Build the UI English-first through Phase 5, add the language layer in Phase 6 with a translation dictionary and a per-user setting, and test every core screen in both directions before calling Phase 6 done.

### G-15 — File uploads and protected downloads in Gradio 🟡

- **Fact.** SRS §11 requires uploads with metadata in SQLite, bytes in a controlled local directory, authorization on every preview/download/delete, extension allowlist, MIME validation, random safe storage names, no path traversal, no executables, preview of safe types only.
- **Risk.** Gradio's file components are convenient for upload but its download/preview paths must not expose `storage/uploads/` as a static directory. Every download must go through a service method that checks the permission first.
- **Mitigation.** Store only metadata + a random storage name in the DB; serve bytes through an authorized handler; validate MIME by content where practical, not just extension.

### G-16 — Bilingual UI + PDF + charts vs the approved stack 🟢

- **Fact.** Charts must use "Gradio-compatible capabilities" (SRS §13.4); PDF/Excel export is optional and only if it does not violate course constraints (SRS §13.4, §25.15). CSV is the mandatory export.
- **Resolution proposed.** Charts via Gradio's native plot components (matplotlib is a Gradio dependency already, so no new framework is introduced). PDF/Excel only after the G-08 answer; otherwise CSV + printable HTML.

### G-17 — Smart scheduling / revenue optimization (SRS §3.8) 🟢

- **Fact.** The feature must be advisory only, deterministic, explainable, never auto-change a booking, never shorten without customer agreement, never go below package minimum duration, and must not claim AI/ML.
- **Risk.** Low: a transparent scoring heuristic (e.g. prefer slots that fill gaps between existing bookings) satisfies the requirement. The risk is scope creep into a fake "AI" feature.
- **Mitigation.** Implement as a small, documented heuristic in Phase 3 or 6 with its reasoning shown to the user; label it "suggestion", never "AI".

### G-18 — Financial definitions and the simplified accounting basis 🟡

- **Fact.** SRS §8.6 requires Contract Value, Collected, Outstanding, Expenses, Refunds, Cash Flow and Profit to be kept distinct, with the simplified revenue/profit recognition rule documented. Profit is *not* cash collected minus expenses unless the documented rule says so.
- **Risk.** The most likely grading error is mislabelling: showing "profit" where the number is really "cash collected minus expenses" without saying so.
- **Mitigation.** Document the accounting basis in `docs/ASSUMPTIONS_AND_DECISIONS.md` and in the README in Phase 5; every financial report labels its formulas and date basis.

### G-19 — `Rescheduled` as a status vs a timeline event 🟢

- **Fact.** SRS §3.3 lists `Rescheduled` among lifecycle values but then recommends *not* using it as a permanent current status: preserve the commercial state (Pending/Confirmed) and record reschedules as timeline events.
- **Resolution proposed.** Follow the recommendation: `status` stays Pending/Confirmed; each reschedule appends to `booking_history`/`reschedule_history`. This is a documented normalization, not a rule change.

### G-20 — Employees vs login accounts: one table or two? 🟢

- **Fact.** SRS §18.1 lists `employees` (with `employee_roles`) and §7.1 describes staff profiles with username/account link, availability and time off. The SRS does not say whether a login account and a staff profile are one row or two linked rows.
- **Resolution proposed.** One `employees` row is both the login account and the staff profile (username, password hash, role links, availability, active flag). Simpler, matches the SRS's own example structure, and avoids a join on every login. Recorded as a decision in Phase 1; reversible if the instructor prefers separate tables.

## 4. Ambiguities that need an answer (not a guess)

| ID | Question | Who can answer | Blocks |
|---|---|---|---|
| A-01 | Is a wedding hall accepted as a custom project topic? (Deck lists 16 ideas only; slide 135 says "Pick one idea".) | Instructor | Academic compliance — everything |
| A-02 | Submission date, submission format, grading weights, is a GitHub link acceptable? (Not in the deck.) | Instructor | Delivery planning |
| A-03 | Is a PDF library permitted for contract export? If not, is printable HTML + CSV acceptable? | Instructor / course rules | Phase 4 (contracts) |
| A-04 | Is any extra dependency permitted at all beyond Gradio (e.g. a crypto library for field encryption)? | Instructor / course rules | Phase 1 (FR-014 decision) |
| A-05 | Real venue data: hall names, capacities, base prices, opening hours, packages, services, pricing rules? (Neither document contains them.) | Ahmed | Phase 2 seed data |
| A-06 | Buffer policy: must the buffer fit inside opening hours, or only the event itself? (SRS §4.2 says define it; default assumption is event-inside-hours, buffer must not overlap another booking or closed block.) | Ahmed (decision) + instructor if graded | Phase 3 availability |
| A-07 | Profit recognition basis: which simplified rule counts as "revenue" for the profit report? (SRS §8.6 requires it documented.) | Ahmed (decision) | Phase 5 reports |
| A-08 | Demo dataset size for the ≤2 s / ≤3 s performance targets? (SRS §16.1 says "agreed demo/test dataset" — no dataset is agreed yet.) | Ahmed (decision) | Phase 7 performance check |

## 5. Risk register (summary)

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| G-01 | Instructor rejects wedding hall as a topic | Unknown — unconfirmed | Critical | Ask early; Hotel Booking Desk fallback reuses the same design |
| G-06 | No deadline/format/weights known | Certain (not in deck) | High | Ask early; assume slide-2 delivery |
| G-07 | Solo scope too large for one term | Medium | High | Phase order reaches deck compliance by Phase 3; trim 6–7 consciously if needed |
| G-12 | Race condition double-books a hall | Medium without the transaction design | Critical | `BEGIN IMMEDIATE` + re-check + race test |
| G-08 | PDF library not permitted | Unknown | Medium | Ask before Phase 4; HTML fallback ready |
| G-14 | RTL layout bugs in Gradio | Medium | Medium | English-first, RTL pass in Phase 6, test both directions |
| G-11 | Holds expire only on access, not at the exact second | Certain by design | Low | Documented lazy-expiry; countdown shown on dashboard |
| G-15 | Upload directory exposed or traversal bug | Low with the design | High | Authorized handler only; tests for traversal/executable/unauthorized download |
| G-18 | Profit mislabelled | Medium | Medium | Documented accounting basis; labelled formulas |

## 6. What is NOT a gap

- **No conflict between the SRS's stack and the deck.** The deck's SQLite + OOP + 3-tier + browser screens are exactly the SRS stack (§0.1). The SRS adds Gradio by name where the deck only implies it (G-04).
- **No conflict on test count.** The SRS's 22 tests include the deck's 3 console cases (G-03).
- **No conflict on CSV.** Both require exactly one CSV export as a minimum (deck slide 2; SRS §13.4).
- **No team/scaffold issues in this restart.** The project is solo on Ahmed's own private repo; there is no team repo, no shared scaffold and no Tkinter placeholder to preserve. Git is optional per SRS §0.3, but the repo already exists and is used.
