"""Initial data (Phase 1): roles, permissions, settings, demo accounts.
Phase 2 adds the example catalog (halls, event types, services,
packages, pricing rules) — clearly-marked EXAMPLES, editable by the
Owner (see config/defaults.py EXAMPLE_*).

Idempotent — running seed twice does not duplicate rows. Only Phase 1
data is seeded here; halls/packages/services are seeded in Phase 2.
Demo passwords are in config/defaults.py and must be changed before
any real use (see README).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

from ..config.defaults import (
    DEFAULT_CONTRACT_TEMPLATE,
    DEFAULT_DASHBOARD_WIDGETS,
    DEFAULT_NOTIFICATION_CONFIG,
    DEFAULT_SETTINGS,
    DEMO_USERS,
    EXAMPLE_CUSTOMERS,
    EXAMPLE_EVENT_TYPES,
    EXAMPLE_HALLS,
    EXAMPLE_PACKAGES,
    EXAMPLE_PRICING_RULES,
    EXAMPLE_SERVICES,
    EXAMPLE_TASK_TEMPLATES,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    SEED_ROLES,
)
from ..utils.logging_config import get_logger
from .database import DatabaseManager

logger = get_logger(__name__)

# pbkdf2_hmac parameters must match auth_service.py.
HASH_ALGORITHM = "sha256"
HASH_ITERATIONS = 600_000


def hash_password(password: str, salt_hex: str | None = None,
                  iterations: int = HASH_ITERATIONS) -> tuple[str, str]:
    """Return (password_hash_hex, salt_hex) using PBKDF2-HMAC-SHA256."""
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(HASH_ALGORITHM, password.encode("utf-8"), salt, iterations)
    return digest.hex(), salt.hex()


def _package_id_by_name(conn, name: str | None) -> int | None:
    """Resolve a package id by name (None when the template applies to
    every package)."""
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM packages WHERE name = ?", (name,)).fetchone()
    return int(row["id"]) if row else None


def seed(db: DatabaseManager, iterations: int = HASH_ITERATIONS) -> dict[str, int]:
    """Seed roles/permissions/settings/demo users + example catalog.
    Returns row counts."""
    counts = {"roles": 0, "permissions": 0, "role_permissions": 0, "users": 0,
              "settings": 0, "halls": 0, "event_types": 0, "packages": 0,
              "services": 0, "pricing_rules": 0}
    with db.transaction() as conn:
        role_ids: dict[str, int] = {}
        for role in SEED_ROLES:
            cur = conn.execute(
                "INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?)",
                (role["name"], role["description"]),
            )
            counts["roles"] += cur.rowcount
            row = conn.execute("SELECT id FROM roles WHERE name = ?", (role["name"],)).fetchone()
            role_ids[role["name"]] = int(row["id"])

        perm_ids: dict[str, int] = {}
        for code, description in PERMISSIONS.items():
            cur = conn.execute(
                "INSERT OR IGNORE INTO permissions (code, description) VALUES (?, ?)",
                (code, description),
            )
            counts["permissions"] += cur.rowcount
            row = conn.execute("SELECT id FROM permissions WHERE code = ?", (code,)).fetchone()
            perm_ids[code] = int(row["id"])

        for role_name, codes in ROLE_PERMISSIONS.items():
            if role_name not in role_ids:
                continue
            for code in codes:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    (role_ids[role_name], perm_ids[code]),
                )
                counts["role_permissions"] += cur.rowcount
        # Owner gets every permission.
        for code, pid in perm_ids.items():
            cur = conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                (role_ids["Owner"], pid),
            )
            counts["role_permissions"] += cur.rowcount

        for key, value in DEFAULT_SETTINGS.items():
            cur = conn.execute(
                "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            counts["settings"] += cur.rowcount

        for username, (full_name, role_name, password) in DEMO_USERS.items():
            existing = conn.execute(
                "SELECT id FROM employees WHERE username = ?", (username,)
            ).fetchone()
            if existing:
                continue
            pw_hash, salt = hash_password(password, iterations=iterations)
            cur = conn.execute(
                """INSERT INTO employees
                   (username, password_hash, password_salt, full_name, is_active)
                   VALUES (?, ?, ?, ?, 1)""",
                (username, pw_hash, salt, full_name),
            )
            counts["users"] += cur.rowcount
            employee_id = int(cur.lastrowid)
            conn.execute(
                "INSERT OR IGNORE INTO employee_roles (employee_id, role_id) VALUES (?, ?)",
                (employee_id, role_ids[role_name]),
            )

        # -- Phase 2: example catalog (INSERT OR IGNORE by unique name) --
        hall_ids: dict[str, int] = {}
        for hall in EXAMPLE_HALLS:
            cur = conn.execute(
                """INSERT OR IGNORE INTO halls
                   (name, description, capacity, base_price, status,
                    facilities_json, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (hall["name"], hall["description"], hall["capacity"],
                 hall["base_price_egp"] * 100, hall["status"],
                 json.dumps(hall["facilities"], ensure_ascii=False), hall["notes"]),
            )
            counts["halls"] += cur.rowcount
            row = conn.execute("SELECT id FROM halls WHERE name = ?", (hall["name"],)).fetchone()
            hall_ids[hall["name"]] = int(row["id"])
            for hours in hall["weekly_hours"]:
                conn.execute(
                    """INSERT OR IGNORE INTO hall_weekly_hours
                       (hall_id, weekday, open_time, close_time, is_closed)
                       VALUES (?, ?, ?, ?, 0)""",
                    (hall_ids[hall["name"]], hours["weekday"],
                     hours["open"], hours["close"]),
                )

        event_type_ids: dict[str, int] = {}
        for event_type in EXAMPLE_EVENT_TYPES:
            cur = conn.execute(
                "INSERT OR IGNORE INTO event_types (name, default_duration_min) VALUES (?, ?)",
                (event_type["name"], event_type["default_duration_min"]),
            )
            counts["event_types"] += cur.rowcount
            row = conn.execute(
                "SELECT id FROM event_types WHERE name = ?", (event_type["name"],)).fetchone()
            event_type_ids[event_type["name"]] = int(row["id"])

        service_ids: dict[str, int] = {}
        for service in EXAMPLE_SERVICES:
            cur = conn.execute(
                """INSERT OR IGNORE INTO services
                   (name, description, pricing_method, unit_price)
                   VALUES (?, ?, ?, ?)""",
                (service["name"], service["description"],
                 service["pricing_method"], service["unit_price_egp"] * 100),
            )
            counts["services"] += cur.rowcount
            row = conn.execute(
                "SELECT id FROM services WHERE name = ?", (service["name"],)).fetchone()
            service_ids[service["name"]] = int(row["id"])
            # Example services are offered in every example hall.
            for hall_id in hall_ids.values():
                conn.execute(
                    "INSERT OR IGNORE INTO service_halls (service_id, hall_id) VALUES (?, ?)",
                    (service_ids[service["name"]], hall_id),
                )

        for package in EXAMPLE_PACKAGES:
            cur = conn.execute(
                """INSERT OR IGNORE INTO packages
                   (name, description, base_price, default_duration_min,
                    min_duration_min, included_guests, extra_guest_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (package["name"], package["description"],
                 package["base_price_egp"] * 100,
                 package["default_duration_min"], package["min_duration_min"],
                 package["included_guests"], package["extra_guest_price_egp"] * 100),
            )
            counts["packages"] += cur.rowcount
            row = conn.execute(
                "SELECT id FROM packages WHERE name = ?", (package["name"],)).fetchone()
            package_id = int(row["id"])
            for hall_name in package["halls"]:
                conn.execute(
                    "INSERT OR IGNORE INTO package_halls (package_id, hall_id) VALUES (?, ?)",
                    (package_id, hall_ids[hall_name]),
                )
            for type_name in package["event_types"]:
                conn.execute(
                    "INSERT OR IGNORE INTO package_event_types (package_id, event_type_id) VALUES (?, ?)",
                    (package_id, event_type_ids[type_name]),
                )
            for service_name in package["services"]:
                conn.execute(
                    "INSERT OR IGNORE INTO package_services (package_id, service_id) VALUES (?, ?)",
                    (package_id, service_ids[service_name]),
                )

        for rule in EXAMPLE_PRICING_RULES:
            # pricing_rules has no UNIQUE(name), so check first — seed
            # must stay idempotent across app restarts.
            existing = conn.execute(
                "SELECT id FROM pricing_rules WHERE name = ?", (rule["name"],)).fetchone()
            if existing:
                continue
            cur = conn.execute(
                """INSERT INTO pricing_rules
                   (name, weekday, adjustment_type, adjustment_value)
                   VALUES (?, ?, ?, ?)""",
                (rule["name"], rule["weekday"],
                 rule["adjustment_type"], rule["adjustment_value"]),
            )
            counts["pricing_rules"] += cur.rowcount

        # -- Phase 3: example customers (INSERT OR IGNORE by normalized
        # phone — customers has no UNIQUE on the normalized columns, so
        # check first to stay idempotent). --
        from ..utils.validators import normalize_email, normalize_phone
        for customer in EXAMPLE_CUSTOMERS:
            phone_norm = normalize_phone(customer["phone"])
            existing = conn.execute(
                "SELECT id FROM customers WHERE phone_normalized = ?",
                (phone_norm,)).fetchone()
            if existing:
                continue
            cur = conn.execute(
                """INSERT INTO customers
                   (full_name, phone, phone_normalized, email,
                    email_normalized, address, national_id, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (customer["full_name"], phone_norm, phone_norm,
                 customer["email"], normalize_email(customer["email"])
                 if customer["email"] else "",
                 customer["address"], customer["national_id"],
                 customer["notes"]),
            )
            counts["customers"] = counts.get("customers", 0) + cur.rowcount

        # -- Phase 4: notification config + default contract template
        # (settings rows; INSERT OR IGNORE keeps seed idempotent). --
        cur = conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
            ("notification.config",
             json.dumps(DEFAULT_NOTIFICATION_CONFIG, ensure_ascii=False)),
        )
        counts["settings"] += cur.rowcount
        # Template keys are slug(name) (contract_service.TEMPLATE_PREFIX
        # + slug), so the seeded template's key must match its name.
        template_key = "contract.template." + re.sub(
            r"[^a-z0-9]+", "_",
            DEFAULT_CONTRACT_TEMPLATE["name"].strip().lower()).strip("_")
        # Databases seeded before Phase 4 stored the template under
        # 'contract.template.standard': rename that row instead of
        # creating a second template with the same name.
        old_row = conn.execute(
            "SELECT value FROM system_settings "
            "WHERE key = 'contract.template.standard'").fetchone()
        new_row = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?",
            (template_key,)).fetchone()
        if old_row is not None and new_row is None:
            conn.execute(
                "UPDATE system_settings SET key = ? "
                "WHERE key = 'contract.template.standard'", (template_key,))
        cur = conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
            (template_key,
             json.dumps(DEFAULT_CONTRACT_TEMPLATE, ensure_ascii=False)),
        )
        counts["settings"] += cur.rowcount

        # -- Phase 6: dashboard widget availability per role (SRS §13.2).
        # The Owner can change it; INSERT OR IGNORE keeps seed idempotent.
        cur = conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
            ("dashboard.widgets",
             json.dumps(DEFAULT_DASHBOARD_WIDGETS, ensure_ascii=False)),
        )
        counts["settings"] += cur.rowcount

        # -- Phase 4: example task template (INSERT OR IGNORE by name). --
        for template in EXAMPLE_TASK_TEMPLATES:
            # task_templates has no UNIQUE(name), so check first.
            existing = conn.execute(
                "SELECT id FROM task_templates WHERE name = ?",
                (template["name"],)).fetchone()
            if existing is None:
                cur = conn.execute(
                    """INSERT INTO task_templates
                       (name, event_type_id, package_id) VALUES (?, ?, ?)""",
                    (template["name"],
                     event_type_ids.get(template.get("event_type")),
                     _package_id_by_name(conn, template.get("package"))),
                )
                counts["task_templates"] = counts.get("task_templates", 0) + cur.rowcount
            row = conn.execute(
                "SELECT id FROM task_templates WHERE name = ?",
                (template["name"],)).fetchone()
            template_id = int(row["id"])
            for item in template["items"]:
                # task_template_items has no UNIQUE constraint, so check
                # first — seed must stay idempotent across app restarts.
                existing = conn.execute(
                    """SELECT id FROM task_template_items
                       WHERE template_id = ? AND name = ?""",
                    (template_id, item["name"])).fetchone()
                if existing:
                    continue
                conn.execute(
                    """INSERT INTO task_template_items
                       (template_id, name, description, default_due_offset_min,
                        priority)
                       VALUES (?, ?, ?, ?, ?)""",
                    (template_id, item["name"], item["description"],
                     item["due_offset_min"], item["priority"]),
                )
    logger.info("Seed complete: %s", counts)
    _seed_example_quotation(db)
    return counts


def _seed_example_quotation(db: DatabaseManager) -> None:
    """One Draft example quotation for the first example customer
    (Main Hall, Classic Wedding package, next Friday 18:00–23:00), so
    the quotation → booking flow is demoable without inventing real
    data. Runs AFTER the main seed transaction (the quotation service
    needs the committed catalog rows). Idempotent: skipped when the
    customer already has a quotation. Never fails the seed — a
    quotation that cannot be created is logged, not fatal."""
    # Local import: data.seed must not import logic at module level.
    from datetime import datetime, timedelta

    from ..logic.models import Employee
    from ..logic.quotation_service import QuotationService

    try:
        customer = db.query_one(
            "SELECT * FROM customers WHERE full_name = ?",
            ("Ahmed Hassan (example)",))
        if customer is None:
            return
        existing = db.query_one(
            "SELECT id FROM quotations WHERE customer_id = ? LIMIT 1",
            (int(customer["id"]),))
        if existing:
            return
        hall = db.query_one(
            "SELECT id FROM halls WHERE name = ?", ("Main Hall (example)",))
        package = db.query_one(
            "SELECT id FROM packages WHERE name = ?", ("Classic Wedding (example)",))
        event_type = db.query_one(
            "SELECT id FROM event_types WHERE name = ?", ("Wedding",))
        owner = db.query_one(
            "SELECT id FROM employees WHERE username = 'owner'")
        if not all((hall, package, event_type, owner)):
            return
        today = datetime.now().date()
        days_ahead = (4 - today.weekday()) % 7 or 7  # next Friday
        friday = today + timedelta(days=days_ahead)
        start = datetime(friday.year, friday.month, friday.day, 18, 0)
        end = datetime(friday.year, friday.month, friday.day, 23, 0)
        employee = Employee(id=int(owner["id"]), username="owner",
                            full_name="Owner (Demo)")
        QuotationService(db).create_quotation(
            employee, customer_id=int(customer["id"]),
            hall_id=int(hall["id"]),
            event_type_id=int(event_type["id"]),
            package_id=int(package["id"]),
            start_at=start, end_at=end, guests=200)
        logger.info("Example quotation seeded for Ahmed Hassan (example).")
    except Exception:  # pragma: no cover - seed must never fail the app
        logger.warning("Example quotation could not be seeded.", exc_info=True)
