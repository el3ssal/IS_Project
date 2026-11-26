-- Migration 005 (Phase 8): Remember Me tokens, service-change history,
-- National ID blind index, notification render data.
-- NOTE: no semicolons anywhere in comments, the runner splits on them.

CREATE TABLE IF NOT EXISTS remember_me_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS booking_service_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    employee_id INTEGER REFERENCES employees(id),
    old_services_json TEXT NOT NULL DEFAULT '[]',
    new_services_json TEXT NOT NULL DEFAULT '[]',
    old_total INTEGER NOT NULL DEFAULT 0,
    new_total INTEGER NOT NULL DEFAULT 0,
    delta INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    refund_id INTEGER REFERENCES refunds(id),
    contract_action TEXT NOT NULL DEFAULT 'note'
        CHECK (contract_action IN ('note', 'new_version')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE customers ADD COLUMN national_id_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE notifications ADD COLUMN data_json TEXT NOT NULL DEFAULT '{}'
