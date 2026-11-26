-- Migration 003 (Phase 5) — schema v2 -> v3.
--   * reschedule_history.due_at: due date for a price-increase
--     difference (SRS §8.9 — the difference becomes outstanding with a
--     due date and no payment is required before the reschedule is
--     approved).
--   * no_show_records.fee_amount / refundable_amount: the no-show
--     financial policy outcome (SRS §8.10), frozen at recording time.
-- Idempotent: the runner in data/database.py skips ALTER statements
-- whose column already exists (fresh DBs get the columns from
-- schema.sql itself), so re-running this file is safe.
ALTER TABLE reschedule_history ADD COLUMN due_at TEXT;
ALTER TABLE no_show_records ADD COLUMN fee_amount INTEGER NOT NULL DEFAULT 0;
ALTER TABLE no_show_records ADD COLUMN refundable_amount INTEGER NOT NULL DEFAULT 0;
