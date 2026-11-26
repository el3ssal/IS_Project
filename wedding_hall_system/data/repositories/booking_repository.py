"""Quotation / booking / payment queries (Phase 3, data tier).

Repositories own SQL — all queries are parameterised. Functions that
take ``conn`` run on the caller's transaction connection (required for
booking creation: the availability re-check and the insert must share
one BEGIN IMMEDIATE transaction, SRS §3.2); without ``conn`` they use
a short-lived connection from the manager.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from ..database import DatabaseManager


# ---------------------------------------------------------------- quotations


def insert_quotation(db: DatabaseManager, *, number: str, customer_id: int,
                     hall_id: int, event_type_id: int | None,
                     package_id: int | None, start_at: str, end_at: str,
                     guest_count: int, total_amount: int,
                     valid_until: str | None, created_by: int | None,
                     conn=None) -> int:
    sql = """INSERT INTO quotations
             (number, customer_id, hall_id, event_type_id, package_id,
              start_at, end_at, guest_count, total_amount, status,
              valid_until, created_by)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)"""
    params = (number, customer_id, hall_id, event_type_id, package_id,
              start_at, end_at, guest_count, total_amount, valid_until,
              created_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_quotation(db: DatabaseManager, quotation_id: int,
                  conn=None) -> dict | None:
    sql = "SELECT * FROM quotations WHERE id = ?"
    if conn is not None:
        row = conn.execute(sql, (quotation_id,)).fetchone()
    else:
        row = db.query_one(sql, (quotation_id,))
    return dict(row) if row else None


def list_quotations(db: DatabaseManager, *, customer_id: int | None = None,
                    status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM quotations"
    clauses: list[str] = []
    params: list = []
    if customer_id is not None:
        clauses.append("customer_id = ?")
        params.append(customer_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def update_quotation_status(db: DatabaseManager, quotation_id: int,
                            status: str, conn=None) -> None:
    sql = """UPDATE quotations SET status = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?"""
    if conn is not None:
        conn.execute(sql, (status, quotation_id))
        return
    db.execute(sql, (status, quotation_id))


def insert_quotation_item(db: DatabaseManager, *, quotation_id: int,
                          kind: str, label: str, amount: int,
                          conn=None) -> int:
    sql = """INSERT INTO quotation_items (quotation_id, kind, label, amount)
             VALUES (?, ?, ?, ?)"""
    if conn is not None:
        return int(conn.execute(sql, (quotation_id, kind, label, amount)).lastrowid)
    return db.execute(sql, (quotation_id, kind, label, amount))


def list_quotation_items(db: DatabaseManager, quotation_id: int) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM quotation_items WHERE quotation_id = ? ORDER BY id",
        (quotation_id,),
    )
    return [dict(r) for r in rows]


def count_quotations_on_day(db: DatabaseManager, day_prefix: str,
                            conn=None) -> int:
    sql = "SELECT COUNT(*) AS n FROM quotations WHERE number LIKE ?"
    if conn is not None:
        row = conn.execute(sql, (day_prefix + "%",)).fetchone()
    else:
        row = db.query_one(sql, (day_prefix + "%",))
    return int(row["n"])


# ------------------------------------------------------------------ bookings


def insert_booking(db: DatabaseManager, *, number: str, customer_id: int,
                   hall_id: int, quotation_id: int | None,
                   event_type_id: int | None, package_id: int | None,
                   start_at: str, end_at: str, guest_count: int,
                   total_amount: int, deposit_required: int,
                   hold_expires_at: str, grace_expires_at: str,
                   additional_contact_phone: str, notes: str,
                   created_by: int | None, conn=None,
                   created_at: str | None = None,
                   previous_booking_id: int | None = None) -> int:
    # created_at is passed explicitly by the caller when given: the
    # booking's hold/grace timestamps are computed from the same clock
    # (naive local time). Relying on SQLite's DEFAULT CURRENT_TIMESTAMP
    # (UTC) here made hold - created_at read 50h instead of 48h in the
    # Africa/Cairo timezone (UTC+2).
    sql = """INSERT INTO bookings
             (number, customer_id, hall_id, quotation_id, event_type_id,
              package_id, start_at, end_at, guest_count, status,
              total_amount, deposit_required, deposit_paid,
              hold_expires_at, grace_expires_at,
              additional_contact_phone, notes, created_by, created_at,
              previous_booking_id)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)"""
    params = (number, customer_id, hall_id, quotation_id, event_type_id,
              package_id, start_at, end_at, guest_count, total_amount,
              deposit_required, hold_expires_at, grace_expires_at,
              additional_contact_phone, notes, created_by,
              created_at if created_at is not None
              else datetime.now().isoformat(timespec="seconds"),
              previous_booking_id)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_booking(db: DatabaseManager, booking_id: int, conn=None) -> dict | None:
    sql = "SELECT * FROM bookings WHERE id = ?"
    if conn is not None:
        row = conn.execute(sql, (booking_id,)).fetchone()
    else:
        row = db.query_one(sql, (booking_id,))
    return dict(row) if row else None


def list_bookings(db: DatabaseManager, *, customer_id: int | None = None,
                  hall_id: int | None = None, status: str | None = None,
                  start_from: str | None = None,
                  start_to: str | None = None) -> list[dict]:
    sql = "SELECT * FROM bookings"
    clauses: list[str] = []
    params: list = []
    if customer_id is not None:
        clauses.append("customer_id = ?")
        params.append(customer_id)
    if hall_id is not None:
        clauses.append("hall_id = ?")
        params.append(hall_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if start_from is not None:
        clauses.append("start_at >= ?")
        params.append(start_from)
    if start_to is not None:
        clauses.append("start_at < ?")
        params.append(start_to)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY start_at, id"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def count_bookings_on_day(db: DatabaseManager, day_prefix: str,
                          conn=None) -> int:
    sql = "SELECT COUNT(*) AS n FROM bookings WHERE number LIKE ?"
    if conn is not None:
        row = conn.execute(sql, (day_prefix + "%",)).fetchone()
    else:
        row = db.query_one(sql, (day_prefix + "%",))
    return int(row["n"])


def update_booking_status(db: DatabaseManager, booking_id: int, status: str,
                          conn=None) -> None:
    sql = """UPDATE bookings SET status = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?"""
    if conn is not None:
        conn.execute(sql, (status, booking_id))
        return
    db.execute(sql, (status, booking_id))


def update_booking_deposit_paid(db: DatabaseManager, booking_id: int,
                                deposit_paid: int, conn=None) -> None:
    sql = """UPDATE bookings SET deposit_paid = ?,
             updated_at = CURRENT_TIMESTAMP WHERE id = ?"""
    if conn is not None:
        conn.execute(sql, (deposit_paid, booking_id))
        return
    db.execute(sql, (deposit_paid, booking_id))


def update_booking_notes(db: DatabaseManager, booking_id: int, notes: str,
                         conn=None) -> None:
    sql = """UPDATE bookings SET notes = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?"""
    if conn is not None:
        conn.execute(sql, (notes, booking_id))
        return
    db.execute(sql, (notes, booking_id))


def insert_booking_service(db: DatabaseManager, *, booking_id: int,
                           service_id: int, quantity: int, unit_price: int,
                           amount: int, is_included_in_package: bool,
                           conn=None) -> int:
    sql = """INSERT INTO booking_services
             (booking_id, service_id, quantity, unit_price, amount,
              is_included_in_package)
             VALUES (?, ?, ?, ?, ?, ?)"""
    params = (booking_id, service_id, quantity, unit_price, amount,
              1 if is_included_in_package else 0)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_booking_services(db: DatabaseManager, booking_id: int) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM booking_services WHERE booking_id = ? ORDER BY id",
        (booking_id,),
    )
    return [dict(r) for r in rows]


def insert_booking_price_item(db: DatabaseManager, *, booking_id: int,
                              kind: str, label: str, amount: int,
                              conn=None) -> int:
    sql = """INSERT INTO booking_price_items (booking_id, kind, label, amount)
             VALUES (?, ?, ?, ?)"""
    if conn is not None:
        return int(conn.execute(sql, (booking_id, kind, label, amount)).lastrowid)
    return db.execute(sql, (booking_id, kind, label, amount))


def list_booking_price_items(db: DatabaseManager, booking_id: int) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM booking_price_items WHERE booking_id = ? ORDER BY id",
        (booking_id,),
    )
    return [dict(r) for r in rows]


def insert_booking_history(db: DatabaseManager, *, booking_id: int,
                           employee_id: int | None, action: str,
                           old_value: str = "", new_value: str = "",
                           reason: str = "", conn=None) -> int:
    sql = """INSERT INTO booking_history
             (booking_id, employee_id, action, old_value, new_value, reason)
             VALUES (?, ?, ?, ?, ?, ?)"""
    params = (booking_id, employee_id, action, old_value, new_value, reason)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_booking_history(db: DatabaseManager, booking_id: int) -> list[dict]:
    rows = db.query_all(
        """SELECT * FROM booking_history WHERE booking_id = ?
           ORDER BY created_at, id""",
        (booking_id,),
    )
    return [dict(r) for r in rows]


def list_overdue_pending(db: DatabaseManager, now_iso: str,
                         conn=None) -> list[dict]:
    """Pending bookings whose grace period has passed (SRS §3.4)."""
    sql = """SELECT * FROM bookings
             WHERE status = 'pending' AND grace_expires_at IS NOT NULL
               AND grace_expires_at <= ?
             ORDER BY grace_expires_at"""
    if conn is not None:
        rows = conn.execute(sql, (now_iso,)).fetchall()
        return [dict(r) for r in rows]
    return [dict(r) for r in db.query_all(sql, (now_iso,))]


# ------------------------------------------------------------------ payments


def insert_payment(db: DatabaseManager, *, number: str, booking_id: int,
                   amount: int, method: str, reference: str,
                   idempotency_key: str | None, initiated_at: str | None,
                   confirmed_at: str | None, recorded_by: int | None,
                   conn=None) -> int:
    sql = """INSERT INTO payments
             (number, booking_id, amount, method, reference, status,
              idempotency_key, initiated_at, confirmed_at, recorded_by)
             VALUES (?, ?, ?, ?, ?, 'recorded', ?, ?, ?, ?)"""
    params = (number, booking_id, amount, method, reference,
              idempotency_key, initiated_at, confirmed_at, recorded_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_payment_by_idempotency_key(db: DatabaseManager, key: str,
                                   conn=None) -> dict | None:
    sql = "SELECT * FROM payments WHERE idempotency_key = ?"
    if conn is not None:
        row = conn.execute(sql, (key,)).fetchone()
    else:
        row = db.query_one(sql, (key,))
    return dict(row) if row else None


def list_payments(db: DatabaseManager, booking_id: int) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM payments WHERE booking_id = ? ORDER BY created_at, id",
        (booking_id,),
    )
    return [dict(r) for r in rows]


def sum_recorded_payments(db: DatabaseManager, booking_id: int,
                          conn=None) -> int:
    sql = """SELECT COALESCE(SUM(amount), 0) AS total FROM payments
             WHERE booking_id = ? AND status = 'recorded'"""
    if conn is not None:
        row = conn.execute(sql, (booking_id,)).fetchone()
    else:
        row = db.query_one(sql, (booking_id,))
    return int(row["total"])


def count_payments_on_day(db: DatabaseManager, day_prefix: str,
                          conn=None) -> int:
    sql = "SELECT COUNT(*) AS n FROM payments WHERE number LIKE ?"
    if conn is not None:
        row = conn.execute(sql, (day_prefix + "%",)).fetchone()
    else:
        row = db.query_one(sql, (day_prefix + "%",))
    return int(row["n"])


def insert_cancellation_record(db: DatabaseManager, *, booking_id: int,
                               fee_amount: int, refundable_amount: int,
                               reason: str, requested_by: int | None,
                               approved_by: int | None,
                               conn=None) -> int:
    sql = """INSERT INTO cancellation_records
             (booking_id, fee_amount, refundable_amount, reason,
              requested_by, approved_by)
             VALUES (?, ?, ?, ?, ?, ?)"""
    params = (booking_id, fee_amount, refundable_amount, reason,
              requested_by, approved_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def insert_no_show_record(db: DatabaseManager, *, booking_id: int,
                          reason: str = "", recorded_by: int | None = None,
                          fee_amount: int = 0, refundable_amount: int = 0,
                          conn=None) -> int:
    sql = """INSERT INTO no_show_records
             (booking_id, reason, recorded_by, fee_amount, refundable_amount)
             VALUES (?, ?, ?, ?, ?)"""
    params = (booking_id, reason, recorded_by, fee_amount, refundable_amount)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def update_booking_schedule(db: DatabaseManager, booking_id: int, *,
                            hall_id: int, start_at: str, end_at: str,
                            total_amount: int, deposit_required: int,
                            conn=None) -> None:
    """Apply a reschedule: new hall/time and the re-priced totals
    (SRS §8.9). The old values live in reschedule_history."""
    sql = """UPDATE bookings
             SET hall_id = ?, start_at = ?, end_at = ?,
                 total_amount = ?, deposit_required = ?
             WHERE id = ?"""
    params = (hall_id, start_at, end_at, total_amount, deposit_required,
              booking_id)
    if conn is not None:
        conn.execute(sql, params)
    else:
        db.execute(sql, params)


def update_booking_customer(db: DatabaseManager, booking_id: int,
                            customer_id: int, conn=None) -> None:
    """Apply an ownership transfer (SRS §8.12). Payment rows keep
    their original payer identity; only the booking owner changes."""
    sql = "UPDATE bookings SET customer_id = ? WHERE id = ?"
    if conn is not None:
        conn.execute(sql, (customer_id, booking_id))
    else:
        db.execute(sql, (customer_id, booking_id))


def delete_booking_price_items(db: DatabaseManager, booking_id: int,
                               conn=None) -> None:
    """Remove the current frozen price breakdown (reschedule replaces
    it with the re-priced one; old/new totals stay in
    reschedule_history)."""
    sql = "DELETE FROM booking_price_items WHERE booking_id = ?"
    if conn is not None:
        conn.execute(sql, (booking_id,))
    else:
        db.execute(sql, (booking_id,))


def delete_booking_services(db: DatabaseManager, booking_id: int,
                            conn=None) -> None:
    sql = "DELETE FROM booking_services WHERE booking_id = ?"
    if conn is not None:
        conn.execute(sql, (booking_id,))
    else:
        db.execute(sql, (booking_id,))


def list_pending_holds_expiring_soon(db: DatabaseManager, now_iso: str,
                                     window_end_iso: str,
                                     conn=None) -> list[dict]:
    """Pending bookings whose hold expires inside (now, window_end]
    (SRS §10 hold-expiry warning; lazy, GAP G-11)."""
    sql = """SELECT * FROM bookings
             WHERE status = 'pending' AND hold_expires_at IS NOT NULL
               AND hold_expires_at > ? AND hold_expires_at <= ?
             ORDER BY hold_expires_at"""
    if conn is not None:
        rows = conn.execute(sql, (now_iso, window_end_iso)).fetchall()
    else:
        rows = db.query_all(sql, (now_iso, window_end_iso))
    return [dict(r) for r in rows]


def list_confirmed_starting_soon(db: DatabaseManager, now_iso: str,
                                 window_end_iso: str,
                                 conn=None) -> list[dict]:
    """Confirmed bookings starting inside (now, window_end]
    (SRS §10 event-approaching warning; lazy, GAP G-11)."""
    sql = """SELECT * FROM bookings
             WHERE status = 'confirmed'
               AND start_at > ? AND start_at <= ?
             ORDER BY start_at"""
    if conn is not None:
        rows = conn.execute(sql, (now_iso, window_end_iso)).fetchall()
    else:
        rows = db.query_all(sql, (now_iso, window_end_iso))
    return [dict(r) for r in rows]


def list_audit_for_entity(db: DatabaseManager, entity_type: str,
                          entity_id: int) -> list[dict]:
    rows = db.query_all(
        """SELECT * FROM audit_logs
           WHERE entity_type = ? AND entity_id = ?
           ORDER BY created_at, id""",
        (entity_type, entity_id),
    )
    return [dict(r) for r in rows]


def customer_totals(db: DatabaseManager, customer_id: int) -> dict:
    """Contracted / collected / outstanding for a customer profile
    (SRS §5.1). Contracted = total of bookings that still hold value
    (pending/confirmed/in_progress/completed); collected = recorded
    payments on the customer's bookings; outstanding = contracted −
    collected, floored at 0. Overpayment is reported separately."""
    contracted_row = db.query_one(
        """SELECT COALESCE(SUM(total_amount), 0) AS total FROM bookings
           WHERE customer_id = ?
             AND status IN ('pending', 'confirmed', 'in_progress', 'completed')""",
        (customer_id,),
    )
    collected_row = db.query_one(
        """SELECT COALESCE(SUM(p.amount), 0) AS total FROM payments p
           JOIN bookings b ON b.id = p.booking_id
           WHERE b.customer_id = ? AND p.status = 'recorded'""",
        (customer_id,),
    )
    booking_count = db.query_one(
        "SELECT COUNT(*) AS n FROM bookings WHERE customer_id = ?",
        (customer_id,),
    )
    contracted = int(contracted_row["total"])
    collected = int(collected_row["total"])
    return {
        "booking_count": int(booking_count["n"]),
        "contracted": contracted,
        "collected": collected,
        "outstanding": max(0, contracted - collected),
        "overpaid": max(0, collected - contracted),
    }
