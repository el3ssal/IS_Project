# Diagrams — Wedding Hall Management System

Final UML + DFD diagram set for the **as-built** system (schema v4, Phases 0–7
built; the schema is still v4 — Phase 7 added no migration). Every diagram was drawn from
the code and docs, not from a wish-list:

- `wedding_hall_system/data/schema.sql` — real table / column names
- `wedding_hall_system/logic/models.py` — real model classes / fields
- `wedding_hall_system/logic/*_service.py` — real service class names
- `wedding_hall_system/ui/app.py` + `ui/*_views.py` — real Gradio tabs
- `wedding_hall_system/config/defaults.py` — the 5 roles
- `docs/ARCHITECTURE_REVIEW.md`, `docs/REQUIREMENT_TRACEABILITY_MATRIX.md`

## Files

| File | Diagram | Notes |
|---|---|---|
| `use-case.mmd` | Use Case | Actors: Owner, Manager, Receptionist, Accountant, Staff + Customer (external, **no login**). `<<include>>` only where the code really has it. Includes the Phase 7 calendar use cases and the Owner-only settings edit / backup use cases. |
| `activity-book-venue.mmd` | Activity — "Book a venue" | Quotation → accept → Pending (48h hold) → 30% deposit → Confirmed → contract → event plan/tasks, with error paths (unavailable slot, insufficient deposit, over-capacity, expired hold/quotation). |
| `sequence-booking.mmd` | Sequence — booking creation | Deck swimlanes: User / UI (Gradio) / Service / Database; `BEGIN IMMEDIATE` availability re-check + `alt` frame for conflict rejection. |
| `dfd-context.mmd` | DFD context | System as one process; external entities: 5 employee roles, Customer, Hall. |
| `dfd-level0.mmd` | DFD Level 0 | Processes 1.0–11.0 (P10 Calendar and P11 Settings added in Phase 7); data stores D1–D9 mapped to the real tables. |
| `dfd-level1-bookings.mmd` | DFD Level 1 — Manage Bookings | Availability check, price calculation, hold/deposit, confirmation, cancellation/reschedule. |
| `class.mmd` | Class diagram (appendix) | Real dataclasses from `logic/models.py` with real fields and associations, plus the three Phase 7 service classes (`BackupService`, `SettingsService`, `CalendarService`). |
| `er.mmd` | ER diagram (appendix) | Core 26 entities with real PK/FK relationships from `schema.sql`. |

## What the diagrams deliberately do NOT show

No customer portal, no payment gateway, no email/SMS, no e-signature, no PDF
export (contracts are versioned HTML, printable to PDF from the browser), no
drag-and-drop calendar, no waiting list, no promo codes / loyalty. Phase 7
built the calendar as a table view (FR-057/FR-058): bookings, Pending holds,
maintenance blocks, closures/special hours, with rescheduling done through
the normal `BookingService.reschedule_booking` workflow — never a direct
drag-and-drop move. The Owner Settings screen (FR-067, `SettingsService`)
and the Backup & Restore screen (FR-068, `BackupService`) are built: editing
settings is Owner-only (Manager can view); backup is Owner-only, validates
checksum / integrity / schema version, always makes a safety copy before
restoring, and writes a manifest JSON sidecar while inventorying — not
copying — the `storage/` files. LAN mode: `python main.py --lan` binds
0.0.0.0 with a warning; the default is 127.0.0.1:7860.

## ER diagram scope

The full schema v4 has **54 tables**. The ER diagram draws the core 26
entities; the other 28 tables are listed in the comment header of `er.mmd`
and every relationship drawn is a real foreign key in `schema.sql`.
`contracts.current_version_id` is a plain integer pointer (no FK constraint
in the schema), so it appears as a column only, not as a relationship line.

## Key facts baked into the diagrams

- Hold 48h, grace 24h, buffer 60 min, deposit 30% (`config/settings.py`).
- Booking statuses: pending, confirmed, in_progress, completed, cancelled,
  no_show, expired. Quotation statuses: draft, sent, accepted, expired.
- Cancellation fee tiers are seed defaults (A-43/A-44, Owner to confirm with
  the hall): ≥30 days 10%, 14–29 days 25%, <14 days 50%, capped at amount
  paid. No-show: deposit forfeited by default.
- Money is integer piasters; timestamps are ISO-8601 text (Africa/Cairo).

## How to render

### Option 1 — Mermaid Live Editor (no install)

1. Open <https://mermaid.live>.
2. Paste the contents of a `.mmd` file.
3. Export PNG / SVG from the editor.

### Option 2 — Mermaid CLI (local)

```bash
npm i -g @mermaid-js/mermaid-cli
mmdc -i use-case.mmd -o use-case.png
mmdc -i use-case.mmd -o use-case.svg   # for scalable output
```

Render all eight at once (from this directory):

```bash
for f in *.mmd; do mmdc -i "$f" -o "${f%.mmd}.png"; done
```

### Option 3 — VS Code

Install the "Mermaid Preview" (or "Markdown Preview Mermaid Support")
extension and open any `.mmd` file to preview / export it.

## Validation

Validated and rendered locally on 2026-11-26 with `@mermaid-js/mermaid-cli`
12.2.0 (installed via `npx`, local Puppeteer/Chromium — nothing was uploaded
to any online renderer). All 8 `.mmd` files parse and render; each diagram
has a `.png` and a `.svg` next to its source, rendered with
`mmdc -i <name>.mmd -o <name>.png --size <2000–3000>` (and `.svg` at default
size — SVG is vector, so it stays sharp at any zoom). The two dense diagrams
(`use-case`, `dfd-level0`) are best read from the SVG or the large PNG zoomed
in. Phase 7 update (2026-11-26, same CLI): `use-case`, `dfd-level0` and
`class` were re-rendered after the Phase 7 edits (`--size 3000`, `3000`,
`2600` respectively); the other five diagrams were not touched. Note: when
rendering as root, Puppeteer needs a config file with
`{"args": ["--no-sandbox", "--disable-setuid-sandbox"]}` passed via
`mmdc -p <config.json>`, otherwise Chromium refuses to launch. Two real syntax faults were found and fixed during validation: a `;`
inside a sequence-diagram Note (Mermaid treats it as a statement separator),
and unquoted `(` parentheses in flowchart edge/diamond labels (Mermaid reads
`(` as a shape start).
