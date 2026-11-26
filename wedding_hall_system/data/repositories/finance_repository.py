"""Finance repositories (Phase 5, data tier — SRS §8).

Plain SQL only, no business rules. Every write accepts an optional
``conn`` so the service can run it inside the caller's transaction.
"""
from __future__ import annotations

from ..database import DatabaseManager

# ------------------------------------------------------------------ payments


def get_payment(db: DatabaseManager, payment_id: int, conn=None) -> dict | None:
    sql = "SELECT * FROM payments WHERE id = ?"
    if conn is not None:
        row = conn.execute(sql, (payment_id,)).fetchone()
    else:
        row = db.query_one(sql, (payment_id,))
    return dict(row) if row else None


def update_payment_status(db: DatabaseManager, payment_id: int,
                          status: str, conn=None) -> None:
    sql = "UPDATE payments SET status = ? WHERE id = ?"
    if conn is not None:
        conn.execute(sql, (status, payment_id))
    else:
        db.execute(sql, (status, payment_id))


def insert_correction(db: DatabaseManager, *, original_payment_id: int,
                      correction_type: str, amount: int, reason: str,
                      created_by: int | None, conn=None) -> int:
    sql = """INSERT INTO payment_corrections
             (original_payment_id, correction_type, amount, reason, created_by)
             VALUES (?, ?, ?, ?, ?)"""
    params = (original_payment_id, correction_type, amount, reason, created_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_corrections(db: DatabaseManager, payment_id: int) -> list[dict]:
    rows = db.query_all(
        """SELECT * FROM payment_corrections
           WHERE original_payment_id = ? ORDER BY created_at, id""",
        (payment_id,),
    )
    return [dict(r) for r in rows]


def sum_recorded_payments_in_range(db: DatabaseManager, *,
                                   date_from: str | None = None,
                                   date_to: str | None = None) -> int:
    """Recorded payments confirmed inside [date_from, date_to] (naive
    local ISO timestamps — ``confirmed_at`` is set explicitly at record
    time, unlike ``created_at`` which is SQLite UTC, A-37)."""
    sql = """SELECT COALESCE(SUM(amount), 0) AS total FROM payments
             WHERE status = 'recorded'"""
    params: list = []
    if date_from is not None:
        sql += " AND confirmed_at >= ?"
        params.append(date_from)
    if date_to is not None:
        sql += " AND confirmed_at <= ?"
        params.append(date_to)
    row = db.query_one(sql, tuple(params))
    return int(row["total"])


# ---------------------------------------------------------------- installments


def insert_installment(db: DatabaseManager, *, booking_id: int,
                       sequence: int, amount: int, due_at: str,
                       conn=None) -> int:
    sql = """INSERT INTO installments (booking_id, sequence, amount, due_at)
             VALUES (?, ?, ?, ?)"""
    params = (booking_id, sequence, amount, due_at)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_installments(db: DatabaseManager, booking_id: int,
                      conn=None) -> list[dict]:
    sql = """SELECT * FROM installments WHERE booking_id = ?
             ORDER BY sequence, id"""
    if conn is not None:
        rows = conn.execute(sql, (booking_id,)).fetchall()
    else:
        rows = db.query_all(sql, (booking_id,))
    return [dict(r) for r in rows]


def get_installment(db: DatabaseManager, installment_id: int,
                    conn=None) -> dict | None:
    sql = "SELECT * FROM installments WHERE id = ?"
    if conn is not None:
        row = conn.execute(sql, (installment_id,)).fetchone()
    else:
        row = db.query_one(sql, (installment_id,))
    return dict(row) if row else None


def update_installment(db: DatabaseManager, installment_id: int, *,
                       status: str | None = None,
                       paid_payment_id: int | None = None,
                       conn=None) -> None:
    sets: list[str] = []
    params: list = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if paid_payment_id is not None:
        sets.append("paid_payment_id = ?")
        params.append(paid_payment_id)
    if not sets:
        return
    params.append(installment_id)
    sql = f"UPDATE installments SET {', '.join(sets)} WHERE id = ?"
    if conn is not None:
        conn.execute(sql, tuple(params))
    else:
        db.execute(sql, tuple(params))


def count_installments(db: DatabaseManager, booking_id: int,
                       conn=None) -> int:
    sql = "SELECT COUNT(*) AS n FROM installments WHERE booking_id = ?"
    if conn is not None:
        row = conn.execute(sql, (booking_id,)).fetchone()
    else:
        row = db.query_one(sql, (booking_id,))
    return int(row["n"])


def list_overdue_installments(db: DatabaseManager, now_iso: str,
                              conn=None) -> list[dict]:
    """Pending installments whose due date has passed (SRS §8.4)."""
    sql = """SELECT * FROM installments
             WHERE status = 'pending' AND due_at <= ?
             ORDER BY due_at, id"""
    if conn is not None:
        rows = conn.execute(sql, (now_iso,)).fetchall()
    else:
        rows = db.query_all(sql, (now_iso,))
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ expenses


def insert_expense(db: DatabaseManager, *, amount: int, date: str,
                   category: str, description: str, method: str,
                   scope_type: str, scope_id: int | None,
                   recorded_by: int | None, conn=None) -> int:
    sql = """INSERT INTO expenses
             (amount, date, category, description, method, scope_type,
              scope_id, recorded_by)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (amount, date, category, description, method, scope_type,
              scope_id, recorded_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_expense(db: DatabaseManager, expense_id: int,
                conn=None) -> dict | None:
    sql = "SELECT * FROM expenses WHERE id = ?"
    if conn is not None:
        row = conn.execute(sql, (expense_id,)).fetchone()
    else:
        row = db.query_one(sql, (expense_id,))
    return dict(row) if row else None


def list_expenses(db: DatabaseManager, *, category: str | None = None,
                  scope_type: str | None = None, scope_id: int | None = None,
                  approval_status: str | None = None,
                  payment_status: str | None = None,
                  date_from: str | None = None,
                  date_to: str | None = None) -> list[dict]:
    sql = "SELECT * FROM expenses"
    clauses: list[str] = []
    params: list = []
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if scope_type is not None:
        clauses.append("scope_type = ?")
        params.append(scope_type)
    if scope_id is not None:
        clauses.append("scope_id = ?")
        params.append(scope_id)
    if approval_status is not None:
        clauses.append("approval_status = ?")
        params.append(approval_status)
    if payment_status is not None:
        clauses.append("payment_status = ?")
        params.append(payment_status)
    if date_from is not None:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to is not None:
        clauses.append("date <= ?")
        params.append(date_to)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, id DESC"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def update_expense_approval(db: DatabaseManager, expense_id: int,
                            approval_status: str, conn=None) -> None:
    sql = "UPDATE expenses SET approval_status = ? WHERE id = ?"
    if conn is not None:
        conn.execute(sql, (approval_status, expense_id))
    else:
        db.execute(sql, (approval_status, expense_id))


def update_expense_payment(db: DatabaseManager, expense_id: int,
                           payment_status: str, conn=None) -> None:
    sql = "UPDATE expenses SET payment_status = ? WHERE id = ?"
    if conn is not None:
        conn.execute(sql, (payment_status, expense_id))
    else:
        db.execute(sql, (payment_status, expense_id))


def sum_expenses(db: DatabaseManager, *, approval_status: str | None = None,
                 payment_status: str | None = None,
                 date_from: str | None = None,
                 date_to: str | None = None) -> int:
    sql = "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses"
    clauses: list[str] = []
    params: list = []
    if approval_status is not None:
        clauses.append("approval_status = ?")
        params.append(approval_status)
    if payment_status is not None:
        clauses.append("payment_status = ?")
        params.append(payment_status)
    if date_from is not None:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to is not None:
        clauses.append("date <= ?")
        params.append(date_to)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    row = db.query_one(sql, tuple(params))
    return int(row["total"])


# ------------------------------------------------------------------- refunds


def insert_refund(db: DatabaseManager, *, payment_id: int, booking_id: int,
                  amount: int, reason: str, method: str,
                  created_by: int | None, conn=None) -> int:
    sql = """INSERT INTO refunds
             (payment_id, booking_id, amount, reason, method, created_by)
             VALUES (?, ?, ?, ?, ?, ?)"""
    params = (payment_id, booking_id, amount, reason, method, created_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_refund(db: DatabaseManager, refund_id: int,
               conn=None) -> dict | None:
    sql = "SELECT * FROM refunds WHERE id = ?"
    if conn is not None:
        row = conn.execute(sql, (refund_id,)).fetchone()
    else:
        row = db.query_one(sql, (refund_id,))
    return dict(row) if row else None


def list_refunds(db: DatabaseManager, *, booking_id: int | None = None,
                 payment_id: int | None = None,
                 status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM refunds"
    clauses: list[str] = []
    params: list = []
    if booking_id is not None:
        clauses.append("booking_id = ?")
        params.append(booking_id)
    if payment_id is not None:
        clauses.append("payment_id = ?")
        params.append(payment_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at, id"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def update_refund(db: DatabaseManager, refund_id: int, *,
                  status: str | None = None,
                  approved_by: int | None = None,
                  approved_at: str | None = None,
                  paid_at: str | None = None, conn=None) -> None:
    sets: list[str] = []
    params: list = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if approved_by is not None:
        sets.append("approved_by = ?")
        params.append(approved_by)
    if approved_at is not None:
        sets.append("approved_at = ?")
        params.append(approved_at)
    if paid_at is not None:
        sets.append("paid_at = ?")
        params.append(paid_at)
    if not sets:
        return
    params.append(refund_id)
    sql = f"UPDATE refunds SET {', '.join(sets)} WHERE id = ?"
    if conn is not None:
        conn.execute(sql, tuple(params))
    else:
        db.execute(sql, tuple(params))


def sum_refunds_for_payment(db: DatabaseManager, payment_id: int, *,
                            statuses: tuple[str, ...] = ("approved", "paid"),
                            conn=None) -> int:
    placeholders = ", ".join("?" for _ in statuses)
    sql = f"""SELECT COALESCE(SUM(amount), 0) AS total FROM refunds
              WHERE payment_id = ? AND status IN ({placeholders})"""
    if conn is not None:
        row = conn.execute(sql, (payment_id, *statuses)).fetchone()
    else:
        row = db.query_one(sql, (payment_id, *statuses))
    return int(row["total"])


def sum_refunds_for_booking(db: DatabaseManager, booking_id: int, *,
                            statuses: tuple[str, ...] = ("approved", "paid"),
                            conn=None) -> int:
    placeholders = ", ".join("?" for _ in statuses)
    sql = f"""SELECT COALESCE(SUM(amount), 0) AS total FROM refunds
              WHERE booking_id = ? AND status IN ({placeholders})"""
    if conn is not None:
        row = conn.execute(sql, (booking_id, *statuses)).fetchone()
    else:
        row = db.query_one(sql, (booking_id, *statuses))
    return int(row["total"])


def sum_paid_refunds(db: DatabaseManager, *, date_from: str | None = None,
                     date_to: str | None = None) -> int:
    sql = """SELECT COALESCE(SUM(amount), 0) AS total FROM refunds
             WHERE status = 'paid'"""
    params: list = []
    if date_from is not None:
        sql += " AND paid_at >= ?"
        params.append(date_from)
    if date_to is not None:
        sql += " AND paid_at <= ?"
        params.append(date_to)
    row = db.query_one(sql, tuple(params))
    return int(row["total"])


# ------------------------------------------------- reschedule / transfers


def insert_reschedule_history(db: DatabaseManager, *, booking_id: int,
                              old_hall_id: int | None, new_hall_id: int | None,
                              old_start_at: str, new_start_at: str,
                              old_end_at: str, new_end_at: str,
                              old_amount: int, new_amount: int,
                              approved_by: int | None,
                              due_at: str | None = None,
                              conn=None) -> int:
    sql = """INSERT INTO reschedule_history
             (booking_id, old_hall_id, new_hall_id, old_start_at,
              new_start_at, old_end_at, new_end_at, old_amount,
              new_amount, approved_by, due_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (booking_id, old_hall_id, new_hall_id, old_start_at,
              new_start_at, old_end_at, new_end_at, old_amount,
              new_amount, approved_by, due_at)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_reschedule_history(db: DatabaseManager, booking_id: int) -> list[dict]:
    rows = db.query_all(
        """SELECT * FROM reschedule_history WHERE booking_id = ?
           ORDER BY created_at, id""",
        (booking_id,),
    )
    return [dict(r) for r in rows]


def insert_booking_transfer(db: DatabaseManager, *, booking_id: int,
                            old_customer_id: int, new_customer_id: int,
                            reason: str, requested_by: int | None,
                            approved_by: int | None, conn=None) -> int:
    sql = """INSERT INTO booking_transfers
             (booking_id, old_customer_id, new_customer_id, reason,
              requested_by, approved_by)
             VALUES (?, ?, ?, ?, ?, ?)"""
    params = (booking_id, old_customer_id, new_customer_id, reason,
              requested_by, approved_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_booking_transfers(db: DatabaseManager, booking_id: int) -> list[dict]:
    rows = db.query_all(
        """SELECT * FROM booking_transfers WHERE booking_id = ?
           ORDER BY created_at, id""",
        (booking_id,),
    )
    return [dict(r) for r in rows]


def get_cancellation_record(db: DatabaseManager, booking_id: int,
                            conn=None) -> dict | None:
    sql = """SELECT * FROM cancellation_records WHERE booking_id = ?
             ORDER BY created_at DESC, id DESC LIMIT 1"""
    if conn is not None:
        row = conn.execute(sql, (booking_id,)).fetchone()
    else:
        row = db.query_one(sql, (booking_id,))
    return dict(row) if row else None


def get_no_show_record(db: DatabaseManager, booking_id: int,
                       conn=None) -> dict | None:
    sql = """SELECT * FROM no_show_records WHERE booking_id = ?
             ORDER BY created_at DESC, id DESC LIMIT 1"""
    if conn is not None:
        row = conn.execute(sql, (booking_id,)).fetchone()
    else:
        row = db.query_one(sql, (booking_id,))
    return dict(row) if row else None
