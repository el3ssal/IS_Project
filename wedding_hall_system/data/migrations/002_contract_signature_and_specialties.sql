-- Migration 002 (Phase 4) — schema v1 -> v2.
--   * contracts.signature_date: date the signed copy was signed
--     (recorded when staff upload the signed contract, SRS §6).
--   * employees.specialties: comma-separated staff specialties
--     (SRS §7.1 staff profiles).
-- Idempotent: the runner in data/database.py skips ALTER statements
-- whose column already exists (fresh DBs get the columns from
-- schema.sql itself), so re-running this file is safe.
ALTER TABLE contracts ADD COLUMN signature_date TEXT;
ALTER TABLE employees ADD COLUMN specialties TEXT NOT NULL DEFAULT '';
