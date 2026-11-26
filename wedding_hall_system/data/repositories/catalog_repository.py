"""Hall / catalog / pricing queries (Phase 2, data tier).

Repositories own SQL. Services call these and never build SQL from
user input — all queries are parameterised. Row -> dict helpers keep
services free of ``sqlite3.Row`` handling.
"""
from __future__ import annotations

import json
import sqlite3

from ..database import DatabaseManager

# --------------------------------------------------------------------- halls


def _hall_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    try:
        data["facilities"] = json.loads(data.get("facilities_json") or "[]")
    except (ValueError, TypeError):
        data["facilities"] = []
    data["is_active"] = bool(data["is_active"])
    return data


def list_halls(db: DatabaseManager, *, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM halls"
    if not include_inactive:
        sql += " WHERE is_active = 1 AND status = 'active'"
    sql += " ORDER BY name"
    return [_hall_row(r) for r in db.query_all(sql)]


def get_hall(db: DatabaseManager, hall_id: int, conn=None) -> dict | None:
    """Pass ``conn`` to read inside the caller's transaction (booking
    creation re-checks availability on the same connection that will
    insert, SRS §3.2)."""
    if conn is not None:
        row = conn.execute("SELECT * FROM halls WHERE id = ?", (hall_id,)).fetchone()
    else:
        row = db.query_one("SELECT * FROM halls WHERE id = ?", (hall_id,))
    return _hall_row(row) if row else None


def get_hall_by_name(db: DatabaseManager, name: str) -> dict | None:
    row = db.query_one("SELECT * FROM halls WHERE name = ?", (name,))
    return _hall_row(row) if row else None


def insert_hall(db: DatabaseManager, *, name: str, description: str, capacity: int,
                base_price: int, status: str, facilities_json: str,
                notes: str, conn=None) -> int:
    sql = """INSERT INTO halls
             (name, description, capacity, base_price, status, facilities_json, notes)
             VALUES (?, ?, ?, ?, ?, ?, ?)"""
    params = (name, description, capacity, base_price, status, facilities_json, notes)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def update_hall(db: DatabaseManager, hall_id: int, *, name: str, description: str,
                capacity: int, base_price: int, status: str,
                facilities_json: str, notes: str, is_active: bool) -> None:
    db.execute(
        """UPDATE halls SET name = ?, description = ?, capacity = ?,
           base_price = ?, status = ?, facilities_json = ?, notes = ?,
           is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (name, description, capacity, base_price, status, facilities_json,
         notes, 1 if is_active else 0, hall_id),
    )


# ------------------------------------------------------------ weekly hours


def list_weekly_hours(db: DatabaseManager, hall_id: int, conn=None) -> list[dict]:
    sql = """SELECT * FROM hall_weekly_hours WHERE hall_id = ?
             ORDER BY weekday, open_time"""
    if conn is not None:
        rows = conn.execute(sql, (hall_id,)).fetchall()
    else:
        rows = db.query_all(sql, (hall_id,))
    return [{**dict(r), "is_closed": bool(r["is_closed"])} for r in rows]


def set_weekly_hours(db: DatabaseManager, hall_id: int, weekday: int,
                     open_time: str, close_time: str, is_closed: bool) -> int:
    """Upsert one weekday row (UNIQUE hall/weekday/open_time)."""
    if is_closed:
        db.execute(
            "DELETE FROM hall_weekly_hours WHERE hall_id = ? AND weekday = ?",
            (hall_id, weekday),
        )
        return db.execute(
            """INSERT INTO hall_weekly_hours
               (hall_id, weekday, open_time, close_time, is_closed)
               VALUES (?, ?, '00:00', '00:00', 1)""",
            (hall_id, weekday),
        )
    db.execute(
        "DELETE FROM hall_weekly_hours WHERE hall_id = ? AND weekday = ? AND is_closed = 1",
        (hall_id, weekday),
    )
    return db.execute(
        """INSERT INTO hall_weekly_hours (hall_id, weekday, open_time, close_time, is_closed)
           VALUES (?, ?, ?, ?, 0)
           ON CONFLICT(hall_id, weekday, open_time) DO UPDATE
           SET close_time = excluded.close_time, is_closed = 0""",
        (hall_id, weekday, open_time, close_time),
    )


def delete_weekly_hours(db: DatabaseManager, hours_id: int) -> None:
    db.execute("DELETE FROM hall_weekly_hours WHERE id = ?", (hours_id,))


# ------------------------------------------------------------ special hours


def list_special_hours(db: DatabaseManager, hall_id: int) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM hall_special_hours WHERE hall_id = ? ORDER BY date",
        (hall_id,),
    )
    return [{**dict(r), "is_closed": bool(r["is_closed"])} for r in rows]


def get_special_hours(db: DatabaseManager, hall_id: int, date: str,
                      conn=None) -> dict | None:
    sql = "SELECT * FROM hall_special_hours WHERE hall_id = ? AND date = ?"
    if conn is not None:
        row = conn.execute(sql, (hall_id, date)).fetchone()
    else:
        row = db.query_one(sql, (hall_id, date))
    return {**dict(row), "is_closed": bool(row["is_closed"])} if row else None


def insert_special_hours(db: DatabaseManager, *, hall_id: int, date: str,
                         open_time: str | None, close_time: str | None,
                         is_closed: bool, reason: str) -> int:
    return db.execute(
        """INSERT INTO hall_special_hours
           (hall_id, date, open_time, close_time, is_closed, reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (hall_id, date, open_time, close_time, 1 if is_closed else 0, reason),
    )


def delete_special_hours(db: DatabaseManager, special_id: int) -> None:
    db.execute("DELETE FROM hall_special_hours WHERE id = ?", (special_id,))


# --------------------------------------------------------------- maintenance


def list_maintenance(db: DatabaseManager, hall_id: int | None = None) -> list[dict]:
    if hall_id is None:
        rows = db.query_all(
            "SELECT * FROM maintenance_blocks ORDER BY start_at DESC"
        )
    else:
        rows = db.query_all(
            "SELECT * FROM maintenance_blocks WHERE hall_id = ? ORDER BY start_at DESC",
            (hall_id,),
        )
    return [dict(r) for r in rows]


def overlapping_maintenance(db: DatabaseManager, hall_id: int,
                            start_at: str, end_at: str,
                            conn=None) -> list[dict]:
    """Maintenance blocks overlapping [start_at, end_at) (half-open)."""
    sql = """SELECT * FROM maintenance_blocks
             WHERE hall_id = ? AND start_at < ? AND end_at > ?
             ORDER BY start_at"""
    if conn is not None:
        rows = conn.execute(sql, (hall_id, end_at, start_at)).fetchall()
    else:
        rows = db.query_all(sql, (hall_id, end_at, start_at))
    return [dict(r) for r in rows]


def insert_maintenance(db: DatabaseManager, *, hall_id: int, type_: str,
                       description: str, start_at: str, end_at: str,
                       recorded_by: int | None) -> int:
    return db.execute(
        """INSERT INTO maintenance_blocks
           (hall_id, type, description, start_at, end_at, recorded_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (hall_id, type_, description, start_at, end_at, recorded_by),
    )


def delete_maintenance(db: DatabaseManager, block_id: int) -> None:
    db.execute("DELETE FROM maintenance_blocks WHERE id = ?", (block_id,))


# --------------------------------------------------------------- event types


def list_event_types(db: DatabaseManager, *, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM event_types"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY name"
    return [{**dict(r), "is_active": bool(r["is_active"])} for r in db.query_all(sql)]


def get_event_type(db: DatabaseManager, event_type_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM event_types WHERE id = ?", (event_type_id,))
    return {**dict(row), "is_active": bool(row["is_active"])} if row else None


def insert_event_type(db: DatabaseManager, *, name: str, default_duration_min: int) -> int:
    return db.execute(
        "INSERT INTO event_types (name, default_duration_min) VALUES (?, ?)",
        (name, default_duration_min),
    )


def update_event_type(db: DatabaseManager, event_type_id: int, *, name: str,
                      default_duration_min: int, is_active: bool) -> None:
    db.execute(
        """UPDATE event_types SET name = ?, default_duration_min = ?,
           is_active = ? WHERE id = ?""",
        (name, default_duration_min, 1 if is_active else 0, event_type_id),
    )


# ------------------------------------------------------------------ packages


def _package_row(db: DatabaseManager, row: sqlite3.Row) -> dict:
    data = {**dict(row), "is_active": bool(row["is_active"])}
    data["hall_ids"] = [
        r["hall_id"] for r in db.query_all(
            "SELECT hall_id FROM package_halls WHERE package_id = ?", (data["id"],)
        )
    ]
    data["event_type_ids"] = [
        r["event_type_id"] for r in db.query_all(
            "SELECT event_type_id FROM package_event_types WHERE package_id = ?",
            (data["id"],),
        )
    ]
    data["service_ids"] = [
        r["service_id"] for r in db.query_all(
            "SELECT service_id FROM package_services WHERE package_id = ?",
            (data["id"],),
        )
    ]
    return data


def list_packages(db: DatabaseManager, *, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM packages"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY name"
    return [_package_row(db, r) for r in db.query_all(sql)]


def get_package(db: DatabaseManager, package_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM packages WHERE id = ?", (package_id,))
    return _package_row(db, row) if row else None


def insert_package(db: DatabaseManager, *, name: str, description: str,
                   base_price: int, default_duration_min: int,
                   min_duration_min: int, included_guests: int,
                   extra_guest_price: int) -> int:
    return db.execute(
        """INSERT INTO packages
           (name, description, base_price, default_duration_min,
            min_duration_min, included_guests, extra_guest_price)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, description, base_price, default_duration_min,
         min_duration_min, included_guests, extra_guest_price),
    )


def update_package(db: DatabaseManager, package_id: int, *, name: str,
                   description: str, base_price: int, default_duration_min: int,
                   min_duration_min: int, included_guests: int,
                   extra_guest_price: int, is_active: bool) -> None:
    db.execute(
        """UPDATE packages SET name = ?, description = ?, base_price = ?,
           default_duration_min = ?, min_duration_min = ?, included_guests = ?,
           extra_guest_price = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (name, description, base_price, default_duration_min, min_duration_min,
         included_guests, extra_guest_price, 1 if is_active else 0, package_id),
    )


def _set_links(db: DatabaseManager, table: str, id_col: str,
               owner_id: int, ids: list[int]) -> None:
    db.execute(f"DELETE FROM {table} WHERE {id_col} = ?", (owner_id,))
    col = {"package_halls": "hall_id",
           "package_event_types": "event_type_id",
           "package_services": "service_id"}[table]
    for other_id in ids:
        db.execute(
            f"INSERT OR IGNORE INTO {table} ({id_col}, {col}) VALUES (?, ?)",
            (owner_id, other_id),
        )


def set_package_halls(db: DatabaseManager, package_id: int, hall_ids: list[int]) -> None:
    _set_links(db, "package_halls", "package_id", package_id, hall_ids)


def set_package_event_types(db: DatabaseManager, package_id: int,
                            event_type_ids: list[int]) -> None:
    _set_links(db, "package_event_types", "package_id", package_id, event_type_ids)


def set_package_services(db: DatabaseManager, package_id: int,
                         service_ids: list[int]) -> None:
    _set_links(db, "package_services", "package_id", package_id, service_ids)


# ------------------------------------------------------------------ services


def _service_row(db: DatabaseManager, row: sqlite3.Row) -> dict:
    data = {**dict(row), "is_active": bool(row["is_active"])}
    data["hall_ids"] = [
        r["hall_id"] for r in db.query_all(
            "SELECT hall_id FROM service_halls WHERE service_id = ?", (data["id"],)
        )
    ]
    return data


def list_services(db: DatabaseManager, *, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM services"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY name"
    return [_service_row(db, r) for r in db.query_all(sql)]


def get_service(db: DatabaseManager, service_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM services WHERE id = ?", (service_id,))
    return _service_row(db, row) if row else None


def insert_service(db: DatabaseManager, *, name: str, description: str,
                   pricing_method: str, unit_price: int) -> int:
    return db.execute(
        """INSERT INTO services (name, description, pricing_method, unit_price)
           VALUES (?, ?, ?, ?)""",
        (name, description, pricing_method, unit_price),
    )


def update_service(db: DatabaseManager, service_id: int, *, name: str,
                   description: str, pricing_method: str, unit_price: int,
                   is_active: bool) -> None:
    db.execute(
        """UPDATE services SET name = ?, description = ?, pricing_method = ?,
           unit_price = ?, is_active = ? WHERE id = ?""",
        (name, description, pricing_method, unit_price,
         1 if is_active else 0, service_id),
    )


def set_service_halls(db: DatabaseManager, service_id: int, hall_ids: list[int]) -> None:
    db.execute("DELETE FROM service_halls WHERE service_id = ?", (service_id,))
    for hall_id in hall_ids:
        db.execute(
            "INSERT OR IGNORE INTO service_halls (service_id, hall_id) VALUES (?, ?)",
            (service_id, hall_id),
        )


# ------------------------------------------------------------- pricing rules


def list_pricing_rules(db: DatabaseManager, *, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM pricing_rules"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY id"
    return [{**dict(r), "is_active": bool(r["is_active"])} for r in db.query_all(sql)]


def get_pricing_rule(db: DatabaseManager, rule_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM pricing_rules WHERE id = ?", (rule_id,))
    return {**dict(row), "is_active": bool(row["is_active"])} if row else None


def insert_pricing_rule(db: DatabaseManager, *, name: str, hall_id: int | None,
                        package_id: int | None, event_type_id: int | None,
                        weekday: int | None, season: str,
                        adjustment_type: str, adjustment_value: int) -> int:
    return db.execute(
        """INSERT INTO pricing_rules
           (name, hall_id, package_id, event_type_id, weekday, season,
            adjustment_type, adjustment_value)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, hall_id, package_id, event_type_id, weekday, season,
         adjustment_type, adjustment_value),
    )


def update_pricing_rule(db: DatabaseManager, rule_id: int, *, name: str,
                        hall_id: int | None, package_id: int | None,
                        event_type_id: int | None, weekday: int | None,
                        season: str, adjustment_type: str,
                        adjustment_value: int, is_active: bool) -> None:
    db.execute(
        """UPDATE pricing_rules SET name = ?, hall_id = ?, package_id = ?,
           event_type_id = ?, weekday = ?, season = ?, adjustment_type = ?,
           adjustment_value = ?, is_active = ? WHERE id = ?""",
        (name, hall_id, package_id, event_type_id, weekday, season,
         adjustment_type, adjustment_value, 1 if is_active else 0, rule_id),
    )


# ------------------------------------------------------- bookings (Phase 2/3)


def overlapping_bookings(db: DatabaseManager, hall_id: int,
                         start_at: str, end_at: str,
                         exclude_booking_id: int | None = None,
                         conn=None) -> list[dict]:
    """Active bookings whose buffered interval overlaps [start_at, end_at).

    Only statuses that actually hold the hall count: pending (active hold),
    confirmed and in_progress. Cancelled / expired / no-show / completed
    release the time. Half-open interval semantics (SRS §3.2).
    """
    sql = """SELECT id, start_at, end_at, status FROM bookings
             WHERE hall_id = ? AND status IN ('pending', 'confirmed', 'in_progress')
             AND start_at < ? AND end_at > ?"""
    params: list = [hall_id, end_at, start_at]
    if exclude_booking_id is not None:
        sql += " AND id != ?"
        params.append(exclude_booking_id)
    sql += " ORDER BY start_at"
    if conn is not None:
        rows = conn.execute(sql, tuple(params)).fetchall()
    else:
        rows = db.query_all(sql, tuple(params))
    return [dict(r) for r in rows]
