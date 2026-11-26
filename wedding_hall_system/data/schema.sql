-- Wedding Hall Management System — schema v1 (Phase 1, fresh solo restart)
-- SQLite, foreign keys enforced by the application on every connection.
-- Conventions (see docs/ARCHITECTURE_REVIEW.md §3):
--   * Timestamps: TEXT, ISO-8601, venue local time (Africa/Cairo).
--   * Money: INTEGER piasters (1 EGP = 100). Never FLOAT for money.
--   * Soft deactivation via is_active; history is never hard-deleted.
--   * Snapshots (booking_price_items, booking_policy_snapshots,
--     financial_policy_snapshots, contract_versions) are insert-only;
--     no service updates them.
--   * Overlap prevention is NOT a schema constraint (SQLite has no
--     exclusion constraint): it is enforced by availability_service
--     inside a BEGIN IMMEDIATE transaction. The indexes below make
--     that check fast.
--   * Fresh Phase 0 decisions (ARCHITECTURE_REVIEW AR-06..AR-10):
--     booking_policy_snapshots AND financial_policy_snapshots are
--     separate tables; discount_records is its own table; hall
--     facilities are a JSON array in halls.facilities_json; booking
--     notes history and staff-conflict warnings live in audit_logs;
--     contract templates are rows in system_settings (no
--     contract_templates table).
--   * Added columns (ARCHITECTURE_REVIEW §3.5): payments.idempotency_key,
--     bookings.hold_expires_at / grace_expires_at, refunds.approved_by /
--     approved_at, schema_version table.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- identity
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id),
    permission_id INTEGER NOT NULL REFERENCES permissions(id),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    specialties TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until TEXT,
    language TEXT NOT NULL DEFAULT 'ar',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS employee_roles (
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    role_id INTEGER NOT NULL REFERENCES roles(id),
    PRIMARY KEY (employee_id, role_id)
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER REFERENCES employees(id),
    username_attempted TEXT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- customers
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    phone_normalized TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    email_normalized TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    national_id TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customer_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    phone TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1))
);

CREATE TABLE IF NOT EXISTS customer_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    employee_id INTEGER REFERENCES employees(id),
    type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- halls
CREATE TABLE IF NOT EXISTS halls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    base_price INTEGER NOT NULL DEFAULT 0 CHECK (base_price >= 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'maintenance')),
    facilities_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hall_weekly_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id INTEGER NOT NULL REFERENCES halls(id),
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    is_closed INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0, 1)),
    UNIQUE (hall_id, weekday, open_time)
);

CREATE TABLE IF NOT EXISTS hall_special_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id INTEGER NOT NULL REFERENCES halls(id),
    date TEXT NOT NULL,
    open_time TEXT,
    close_time TEXT,
    is_closed INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0, 1)),
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS maintenance_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id INTEGER NOT NULL REFERENCES halls(id),
    type TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL CHECK (end_at > start_at),
    recorded_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------- catalog
CREATE TABLE IF NOT EXISTS event_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    default_duration_min INTEGER NOT NULL DEFAULT 120 CHECK (default_duration_min > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    base_price INTEGER NOT NULL DEFAULT 0 CHECK (base_price >= 0),
    default_duration_min INTEGER NOT NULL DEFAULT 120 CHECK (default_duration_min > 0),
    min_duration_min INTEGER NOT NULL DEFAULT 60 CHECK (min_duration_min > 0),
    included_guests INTEGER NOT NULL DEFAULT 0 CHECK (included_guests >= 0),
    extra_guest_price INTEGER NOT NULL DEFAULT 0 CHECK (extra_guest_price >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS package_halls (
    package_id INTEGER NOT NULL REFERENCES packages(id),
    hall_id INTEGER NOT NULL REFERENCES halls(id),
    PRIMARY KEY (package_id, hall_id)
);

CREATE TABLE IF NOT EXISTS package_event_types (
    package_id INTEGER NOT NULL REFERENCES packages(id),
    event_type_id INTEGER NOT NULL REFERENCES event_types(id),
    PRIMARY KEY (package_id, event_type_id)
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    pricing_method TEXT NOT NULL DEFAULT 'per_event'
        CHECK (pricing_method IN ('per_event', 'per_hour', 'per_guest', 'by_level')),
    unit_price INTEGER NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS package_services (
    package_id INTEGER NOT NULL REFERENCES packages(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    PRIMARY KEY (package_id, service_id)
);

CREATE TABLE IF NOT EXISTS service_halls (
    service_id INTEGER NOT NULL REFERENCES services(id),
    hall_id INTEGER NOT NULL REFERENCES halls(id),
    PRIMARY KEY (service_id, hall_id)
);

CREATE TABLE IF NOT EXISTS pricing_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    hall_id INTEGER REFERENCES halls(id),
    package_id INTEGER REFERENCES packages(id),
    event_type_id INTEGER REFERENCES event_types(id),
    weekday INTEGER CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6),
    season TEXT NOT NULL DEFAULT '',
    adjustment_type TEXT NOT NULL CHECK (adjustment_type IN ('percent', 'fixed')),
    adjustment_value INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discount_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    employee_id INTEGER REFERENCES employees(id),
    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
    percent INTEGER CHECK (percent IS NULL OR (percent >= 0 AND percent <= 100)),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------- quotations/bookings
CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    hall_id INTEGER NOT NULL REFERENCES halls(id),
    event_type_id INTEGER REFERENCES event_types(id),
    package_id INTEGER REFERENCES packages(id),
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL CHECK (end_at > start_at),
    guest_count INTEGER NOT NULL CHECK (guest_count > 0),
    total_amount INTEGER NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'sent', 'accepted', 'expired')),
    valid_until TEXT,
    created_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quotation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quotation_id INTEGER NOT NULL REFERENCES quotations(id),
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    hall_id INTEGER NOT NULL REFERENCES halls(id),
    quotation_id INTEGER REFERENCES quotations(id),
    event_type_id INTEGER REFERENCES event_types(id),
    package_id INTEGER REFERENCES packages(id),
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL CHECK (end_at > start_at),
    guest_count INTEGER NOT NULL CHECK (guest_count > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'in_progress', 'completed',
                          'cancelled', 'no_show', 'expired')),
    total_amount INTEGER NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    deposit_required INTEGER NOT NULL DEFAULT 0 CHECK (deposit_required >= 0),
    deposit_paid INTEGER NOT NULL DEFAULT 0 CHECK (deposit_paid >= 0),
    hold_expires_at TEXT,
    grace_expires_at TEXT,
    additional_contact_phone TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    previous_booking_id INTEGER REFERENCES bookings(id),
    created_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS booking_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price INTEGER NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
    is_included_in_package INTEGER NOT NULL DEFAULT 0 CHECK (is_included_in_package IN (0, 1))
);

CREATE TABLE IF NOT EXISTS booking_price_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS booking_policy_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS booking_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    employee_id INTEGER REFERENCES employees(id),
    action TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reschedule_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    old_hall_id INTEGER REFERENCES halls(id),
    new_hall_id INTEGER REFERENCES halls(id),
    old_start_at TEXT NOT NULL,
    new_start_at TEXT NOT NULL,
    old_end_at TEXT NOT NULL,
    new_end_at TEXT NOT NULL,
    old_amount INTEGER NOT NULL DEFAULT 0,
    new_amount INTEGER NOT NULL DEFAULT 0,
    approved_by INTEGER REFERENCES employees(id),
    due_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS booking_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    old_customer_id INTEGER NOT NULL REFERENCES customers(id),
    new_customer_id INTEGER NOT NULL REFERENCES customers(id),
    reason TEXT NOT NULL DEFAULT '',
    requested_by INTEGER REFERENCES employees(id),
    approved_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------- contracts/files
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL UNIQUE,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    template_name TEXT NOT NULL DEFAULT '',
    current_version_id INTEGER,
    signature_status TEXT NOT NULL DEFAULT 'draft'
        CHECK (signature_status IN ('draft', 'pending_signature', 'partially_signed',
                                    'signed', 'cancelled')),
    signature_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contract_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id),
    version INTEGER NOT NULL CHECK (version > 0),
    body TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (contract_id, version)
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    original_filename TEXT NOT NULL,
    storage_filename TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    category TEXT NOT NULL DEFAULT 'Other',
    uploaded_by INTEGER REFERENCES employees(id),
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1))
);

-- ---------------------------------------------------------------- operations
CREATE TABLE IF NOT EXISTS employee_availability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    kind TEXT NOT NULL DEFAULT 'available'
        CHECK (kind IN ('available', 'unavailable', 'time_off')),
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS event_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_type_id INTEGER REFERENCES event_types(id),
    package_id INTEGER REFERENCES packages(id),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS task_template_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES task_templates(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    default_due_offset_min INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_plan_id INTEGER REFERENCES event_plans(id),
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    service_id INTEGER REFERENCES services(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    assigned_to INTEGER REFERENCES employees(id),
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    due_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    employee_id INTEGER REFERENCES employees(id),
    action TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------------- finance
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL UNIQUE,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    method TEXT NOT NULL
        CHECK (method IN ('cash', 'bank_transfer', 'visa_card', 'instapay', 'other')),
    reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'recorded'
        CHECK (status IN ('recorded', 'reversed', 'void')),
    idempotency_key TEXT UNIQUE,
    initiated_at TEXT,
    confirmed_at TEXT,
    recorded_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payment_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_payment_id INTEGER NOT NULL REFERENCES payments(id),
    correction_type TEXT NOT NULL CHECK (correction_type IN ('reversal', 'void')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    reason TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS installments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    amount INTEGER NOT NULL CHECK (amount > 0),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'overdue')),
    paid_payment_id INTEGER REFERENCES payments(id)
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount INTEGER NOT NULL CHECK (amount > 0),
    date TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'cash',
    scope_type TEXT NOT NULL DEFAULT 'venue'
        CHECK (scope_type IN ('venue', 'hall', 'event')),
    scope_id INTEGER,
    recorded_by INTEGER REFERENCES employees(id),
    approval_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    payment_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (payment_status IN ('pending', 'paid')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    reason TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'cash',
    status TEXT NOT NULL DEFAULT 'pending_approval'
        CHECK (status IN ('pending_approval', 'approved', 'paid', 'rejected', 'cancelled')),
    created_by INTEGER REFERENCES employees(id),
    approved_by INTEGER REFERENCES employees(id),
    approved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS financial_policy_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cancellation_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    fee_amount INTEGER NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    refundable_amount INTEGER NOT NULL DEFAULT 0 CHECK (refundable_amount >= 0),
    reason TEXT NOT NULL DEFAULT '',
    requested_by INTEGER REFERENCES employees(id),
    approved_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS no_show_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    reason TEXT NOT NULL DEFAULT '',
    recorded_by INTEGER REFERENCES employees(id),
    fee_amount INTEGER NOT NULL DEFAULT 0,
    refundable_amount INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -------------------------------------------------------------------- system
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_role_id INTEGER REFERENCES roles(id),
    recipient_employee_id INTEGER REFERENCES employees(id),
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id INTEGER,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER REFERENCES employees(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    old_value_json TEXT NOT NULL DEFAULT '{}',
    new_value_json TEXT NOT NULL DEFAULT '{}',
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backup_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    checksum TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    created_by INTEGER REFERENCES employees(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validated_at TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------------- indexes
CREATE INDEX IF NOT EXISTS idx_bookings_hall_time ON bookings(hall_id, start_at, end_at, status);
CREATE INDEX IF NOT EXISTS idx_bookings_customer ON bookings(customer_id, start_at);
CREATE INDEX IF NOT EXISTS idx_bookings_status_start ON bookings(status, start_at);
CREATE INDEX IF NOT EXISTS idx_bookings_status_hold ON bookings(status, hold_expires_at);
CREATE INDEX IF NOT EXISTS idx_quotations_hall_time ON quotations(hall_id, start_at, end_at, status);
CREATE INDEX IF NOT EXISTS idx_maintenance_hall ON maintenance_blocks(hall_id, start_at, end_at);
CREATE INDEX IF NOT EXISTS idx_hall_special_hours ON hall_special_hours(hall_id, date);
CREATE INDEX IF NOT EXISTS idx_hall_weekly_hours ON hall_weekly_hours(hall_id, weekday);
CREATE INDEX IF NOT EXISTS idx_payments_booking ON payments(booking_id, created_at);
CREATE INDEX IF NOT EXISTS idx_refunds_payment ON refunds(payment_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assigned_to, status, due_at);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_employee ON audit_logs(employee_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_inbox ON notifications(recipient_role_id, is_read, created_at);
CREATE INDEX IF NOT EXISTS idx_customers_phone_norm ON customers(phone_normalized);
CREATE INDEX IF NOT EXISTS idx_customers_email_norm ON customers(email_normalized);
CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_employees_username ON employees(username);
