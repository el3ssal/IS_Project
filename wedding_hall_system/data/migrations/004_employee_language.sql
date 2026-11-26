-- Migration 004 (Phase 6) — schema v3 -> v4.
--   * employees.language: per-user UI language preference ('ar' or
--     'en', SRS §14 and §16.3). The venue default comes from the
--     venue.default_language system setting (seeded 'ar') and an empty
--     per-user value falls back to that default. Existing accounts get
--     the default so no login breaks.
-- Idempotent: the runner in data/database.py skips ALTER statements
-- whose column already exists (fresh DBs get the column from
-- schema.sql itself), so re-running this file is safe.
ALTER TABLE employees ADD COLUMN language TEXT NOT NULL DEFAULT 'ar';
