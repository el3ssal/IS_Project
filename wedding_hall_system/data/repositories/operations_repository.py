"""Event plans / task templates / tasks / staff availability queries
(Phase 4, data tier). Repositories own SQL — all queries are
parameterised. Task template edits never touch already-generated tasks:
generation copies template items into ``tasks`` rows, so a template
change only affects plans generated afterwards (SRS §7.2).
"""
from __future__ import annotations

import sqlite3

from ..database import DatabaseManager


# -------------------------------------------------------------- task templates


def insert_template(db: DatabaseManager, *, name: str, event_type_id: int | None,
                    package_id: int | None, conn=None) -> int:
    sql = """INSERT INTO task_templates (name, event_type_id, package_id)
             VALUES (?, ?, ?)"""
    params = (name, event_type_id, package_id)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def delete_template(db: DatabaseManager, template_id: int, conn=None) -> None:
    """Delete a template and its items. Generated tasks are copies, so
    deleting a template never affects existing event plans (SRS §7.2)."""
    if conn is not None:
        delete_template_items(db, template_id, conn=conn)
        conn.execute("DELETE FROM task_templates WHERE id = ?", (template_id,))
    else:
        delete_template_items(db, template_id)
        db.execute("DELETE FROM task_templates WHERE id = ?", (template_id,))


def get_template(db: DatabaseManager, template_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM task_templates WHERE id = ?", (template_id,))
    return dict(row) if row else None


def get_template_by_name(db: DatabaseManager, name: str) -> dict | None:
    row = db.query_one("SELECT * FROM task_templates WHERE name = ?", (name,))
    return dict(row) if row else None


def list_templates(db: DatabaseManager, *, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM task_templates"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY name"
    return [dict(r) for r in db.query_all(sql)]


def update_template(db: DatabaseManager, template_id: int, *, name: str | None = None,
                    event_type_id: int | None = None, package_id: int | None = None,
                    is_active: bool | None = None, conn=None) -> None:
    sets: list[str] = []
    params: list = []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if event_type_id is not None:
        sets.append("event_type_id = ?")
        params.append(event_type_id)
    if package_id is not None:
        sets.append("package_id = ?")
        params.append(package_id)
    if is_active is not None:
        sets.append("is_active = ?")
        params.append(1 if is_active else 0)
    if not sets:
        return
    params.append(template_id)
    sql = f"UPDATE task_templates SET {', '.join(sets)} WHERE id = ?"
    if conn is not None:
        conn.execute(sql, tuple(params))
    else:
        db.execute(sql, tuple(params))


def insert_template_item(db: DatabaseManager, *, template_id: int, name: str,
                         description: str, default_due_offset_min: int,
                         priority: str, conn=None) -> int:
    sql = """INSERT INTO task_template_items
             (template_id, name, description, default_due_offset_min, priority)
             VALUES (?, ?, ?, ?, ?)"""
    params = (template_id, name, description, default_due_offset_min, priority)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_template_items(db: DatabaseManager, template_id: int) -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM task_template_items WHERE template_id = ? ORDER BY id",
        (template_id,),
    )]


def delete_template_items(db: DatabaseManager, template_id: int, conn=None) -> None:
    sql = "DELETE FROM task_template_items WHERE template_id = ?"
    if conn is not None:
        conn.execute(sql, (template_id,))
    else:
        db.execute(sql, (template_id,))


# ------------------------------------------------------------------ event plans


def get_event_plan(db: DatabaseManager, booking_id: int, conn=None) -> dict | None:
    if conn is not None:
        row = conn.execute(
            "SELECT * FROM event_plans WHERE booking_id = ?",
            (booking_id,)).fetchone()
    else:
        row = db.query_one("SELECT * FROM event_plans WHERE booking_id = ?",
                           (booking_id,))
    return dict(row) if row else None


def insert_event_plan(db: DatabaseManager, booking_id: int, conn=None) -> int:
    if conn is not None:
        return int(conn.execute(
            "INSERT INTO event_plans (booking_id) VALUES (?)",
            (booking_id,)).lastrowid)
    return db.execute("INSERT INTO event_plans (booking_id) VALUES (?)", (booking_id,))


def list_event_plans(db: DatabaseManager) -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM event_plans ORDER BY id DESC")]


# ------------------------------------------------------------------------ tasks


def insert_task(db: DatabaseManager, *, event_plan_id: int | None, booking_id: int,
                service_id: int | None, name: str, description: str,
                assigned_to: int | None, priority: str, due_at: str | None,
                conn=None) -> int:
    sql = """INSERT INTO tasks
             (event_plan_id, booking_id, service_id, name, description,
              assigned_to, priority, due_at, status)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')"""
    params = (event_plan_id, booking_id, service_id, name, description,
              assigned_to, priority, due_at)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_task(db: DatabaseManager, task_id: int, conn=None) -> dict | None:
    if conn is not None:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    else:
        row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return dict(row) if row else None


def list_tasks(db: DatabaseManager, *, booking_id: int | None = None,
               event_plan_id: int | None = None, assigned_to: int | None = None,
               status: str | None = None, priority: str | None = None,
               due_before: str | None = None) -> list[dict]:
    sql = "SELECT * FROM tasks"
    clauses: list[str] = []
    params: list = []
    if booking_id is not None:
        clauses.append("booking_id = ?")
        params.append(booking_id)
    if event_plan_id is not None:
        clauses.append("event_plan_id = ?")
        params.append(event_plan_id)
    if assigned_to is not None:
        clauses.append("assigned_to = ?")
        params.append(assigned_to)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)
    if due_before:
        clauses.append("due_at IS NOT NULL AND due_at <= ?")
        params.append(due_before)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY due_at IS NULL, due_at, id"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def update_task(db: DatabaseManager, task_id: int, *, name: str | None = None,
                description: str | None = None, service_id: int | None = None,
                assigned_to: int | None = None, priority: str | None = None,
                due_at: str | None = None, status: str | None = None,
                conn=None) -> None:
    sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
    params: list = []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if service_id is not None:
        sets.append("service_id = ?")
        params.append(service_id)
    if assigned_to is not None:
        sets.append("assigned_to = ?")
        params.append(assigned_to)
    if priority is not None:
        sets.append("priority = ?")
        params.append(priority)
    if due_at is not None:
        sets.append("due_at = ?")
        params.append(due_at)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    params.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"
    if conn is not None:
        conn.execute(sql, tuple(params))
    else:
        db.execute(sql, tuple(params))


def insert_task_history(db: DatabaseManager, *, task_id: int, employee_id: int | None,
                        action: str, old_value: str, new_value: str,
                        conn=None) -> int:
    sql = """INSERT INTO task_history
             (task_id, employee_id, action, old_value, new_value)
             VALUES (?, ?, ?, ?, ?)"""
    params = (task_id, employee_id, action, old_value, new_value)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_task_history(db: DatabaseManager, task_id: int) -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM task_history WHERE task_id = ? ORDER BY id",
        (task_id,),
    )]


# ------------------------------------------------------- employee availability


def insert_availability(db: DatabaseManager, *, employee_id: int, date: str,
                        start_time: str | None, end_time: str | None,
                        kind: str, note: str) -> int:
    return db.execute(
        """INSERT INTO employee_availability
           (employee_id, date, start_time, end_time, kind, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (employee_id, date, start_time, end_time, kind, note),
    )


def list_availability(db: DatabaseManager, *, employee_id: int | None = None,
                      date: str | None = None) -> list[dict]:
    sql = "SELECT * FROM employee_availability"
    clauses: list[str] = []
    params: list = []
    if employee_id is not None:
        clauses.append("employee_id = ?")
        params.append(employee_id)
    if date:
        clauses.append("date = ?")
        params.append(date)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date, employee_id"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def delete_availability(db: DatabaseManager, availability_id: int) -> None:
    db.execute("DELETE FROM employee_availability WHERE id = ?", (availability_id,))
