"""Search queries (Phase 6, SRS §13.1).

All SQL is parameterized; LIKE patterns escape the caller's wildcards
so a search term can never widen the match. Sorting is restricted to
an allowlist per module — the column name never comes from user input.
Pagination is LIMIT/OFFSET with a separate COUNT query.
"""
from __future__ import annotations

from ...utils.crypto import decrypt_text
from ..database import DatabaseManager

PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _page(page: int, page_size: int) -> tuple[int, int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(PAGE_SIZE_MAX, int(page_size or PAGE_SIZE_DEFAULT)))
    return page, page_size


def _run(db: DatabaseManager, sql: str, count_sql: str, params: list,
         sort: str, allowed_sorts: dict[str, str],
         page: int, page_size: int) -> dict:
    order = allowed_sorts.get(sort or "", allowed_sorts["default"])
    page, page_size = _page(page, page_size)
    total_row = db.query_one(count_sql, tuple(params))
    total = int(total_row["n"]) if total_row else 0
    rows = db.query_all(
        f"{sql} ORDER BY {order} LIMIT ? OFFSET ?",
        tuple(params) + (page_size, (page - 1) * page_size))
    return {"rows": [dict(r) for r in rows], "total": total,
            "page": page, "page_size": page_size}


def search_customers(db: DatabaseManager, *, term: str = "",
                     sort: str = "name", page: int = 1,
                     page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    like = f"%{_escape_like(term.strip())}%" if term.strip() else "%"
    sql = """SELECT * FROM customers
             WHERE full_name LIKE ? ESCAPE '\\'
                OR phone LIKE ? ESCAPE '\\'
                OR email LIKE ? ESCAPE '\\'"""
    count = """SELECT COUNT(*) AS n FROM customers
               WHERE full_name LIKE ? ESCAPE '\\'
                  OR phone LIKE ? ESCAPE '\\'
                  OR email LIKE ? ESCAPE '\\'"""
    params = [like, like, like]
    result = _run(db, sql, count, params, sort,
                  {"name": "full_name, id", "phone": "phone, id",
                   "default": "full_name, id"}, page, page_size)
    # Phase 8 (FR-014): the National ID is encrypted at rest; decrypt
    # here so callers see the same value as everywhere else (services
    # mask it before display, as before).
    for row in result["rows"]:
        if "national_id" in row:
            row["national_id"] = decrypt_text(row["national_id"])
        row.pop("national_id_hash", None)
    return result


def search_bookings(db: DatabaseManager, *, term: str = "",
                    hall_id: int | None = None, status: str | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    sort: str = "date", page: int = 1,
                    page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    clauses = ["(b.number LIKE ? ESCAPE '\\' OR c.full_name LIKE ? ESCAPE '\\')"]
    like = f"%{_escape_like(term.strip())}%" if term.strip() else "%"
    params: list = [like, like]
    if hall_id is not None:
        clauses.append("b.hall_id = ?")
        params.append(hall_id)
    if status:
        clauses.append("b.status = ?")
        params.append(status)
    if date_from:
        clauses.append("substr(b.start_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(b.start_at, 1, 10) <= ?")
        params.append(date_to)
    where = " WHERE " + " AND ".join(clauses)
    sql = ("SELECT b.*, c.full_name AS customer_name FROM bookings b "
           "LEFT JOIN customers c ON c.id = b.customer_id" + where)
    count = ("SELECT COUNT(*) AS n FROM bookings b "
             "LEFT JOIN customers c ON c.id = b.customer_id" + where)
    return _run(db, sql, count, params, sort,
                {"date": "b.start_at, b.id", "number": "b.number, b.id",
                 "status": "b.status, b.start_at, b.id",
                 "default": "b.start_at, b.id"}, page, page_size)


def search_contracts(db: DatabaseManager, *, term: str = "",
                     signature_status: str | None = None,
                     sort: str = "number", page: int = 1,
                     page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    clauses = ["(ct.number LIKE ? ESCAPE '\\' OR c.full_name LIKE ? ESCAPE '\\')"]
    like = f"%{_escape_like(term.strip())}%" if term.strip() else "%"
    params: list = [like, like]
    if signature_status:
        clauses.append("ct.signature_status = ?")
        params.append(signature_status)
    where = " WHERE " + " AND ".join(clauses)
    sql = ("SELECT ct.*, b.number AS booking_number, "
           "c.full_name AS customer_name FROM contracts ct "
           "LEFT JOIN bookings b ON b.id = ct.booking_id "
           "LEFT JOIN customers c ON c.id = b.customer_id" + where)
    count = ("SELECT COUNT(*) AS n FROM contracts ct "
             "LEFT JOIN bookings b ON b.id = ct.booking_id "
             "LEFT JOIN customers c ON c.id = b.customer_id" + where)
    return _run(db, sql, count, params, sort,
                {"number": "ct.number, ct.id", "status": "ct.signature_status, ct.id",
                 "default": "ct.number, ct.id"}, page, page_size)


def search_payments(db: DatabaseManager, *, term: str = "",
                    method: str | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    amount_min: int | None = None,
                    amount_max: int | None = None,
                    sort: str = "date", page: int = 1,
                    page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    clauses = ["(p.number LIKE ? ESCAPE '\\' OR p.reference LIKE ? ESCAPE '\\')"]
    like = f"%{_escape_like(term.strip())}%" if term.strip() else "%"
    params: list = [like, like]
    if method:
        clauses.append("p.method = ?")
        params.append(method)
    if date_from:
        clauses.append("substr(p.confirmed_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(p.confirmed_at, 1, 10) <= ?")
        params.append(date_to)
    if amount_min is not None:
        clauses.append("p.amount >= ?")
        params.append(amount_min)
    if amount_max is not None:
        clauses.append("p.amount <= ?")
        params.append(amount_max)
    where = " WHERE " + " AND ".join(clauses)
    sql = ("SELECT p.*, b.number AS booking_number FROM payments p "
           "LEFT JOIN bookings b ON b.id = p.booking_id" + where)
    count = ("SELECT COUNT(*) AS n FROM payments p "
             "LEFT JOIN bookings b ON b.id = p.booking_id" + where)
    return _run(db, sql, count, params, sort,
                {"date": "p.confirmed_at DESC, p.id",
                 "amount": "p.amount DESC, p.id",
                 "default": "p.confirmed_at DESC, p.id"}, page, page_size)


def search_staff(db: DatabaseManager, *, term: str = "",
                 specialty: str | None = None, status: str | None = None,
                 sort: str = "name", page: int = 1,
                 page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    clauses = ["(e.full_name LIKE ? ESCAPE '\\' OR e.username LIKE ? ESCAPE '\\')"]
    like = f"%{_escape_like(term.strip())}%" if term.strip() else "%"
    params: list = [like, like]
    if specialty:
        clauses.append("e.specialties LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(specialty)}%")
    if status == "active":
        clauses.append("e.is_active = 1")
    elif status == "inactive":
        clauses.append("e.is_active = 0")
    where = " WHERE " + " AND ".join(clauses)
    sql = ("SELECT e.* FROM employees e" + where)
    count = ("SELECT COUNT(*) AS n FROM employees e" + where)
    return _run(db, sql, count, params, sort,
                {"name": "e.full_name, e.id", "default": "e.full_name, e.id"},
                page, page_size)


def search_files(db: DatabaseManager, *, term: str = "",
                 category: str | None = None, entity_type: str | None = None,
                 entity_types: list[str] | None = None,
                 entity_id: int | None = None, uploaded_by: int | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 sort: str = "date", page: int = 1,
                 page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    clauses = ["a.original_filename LIKE ? ESCAPE '\\'"]
    like = f"%{_escape_like(term.strip())}%" if term.strip() else "%"
    params: list = [like]
    if category:
        clauses.append("a.category = ?")
        params.append(category)
    if entity_type:
        clauses.append("a.entity_type = ?")
        params.append(entity_type)
    if entity_types:
        placeholders = ", ".join("?" for _ in entity_types)
        clauses.append(f"a.entity_type IN ({placeholders})")
        params.extend(entity_types)
    if entity_id is not None:
        clauses.append("a.entity_id = ?")
        params.append(entity_id)
    if uploaded_by is not None:
        clauses.append("a.uploaded_by = ?")
        params.append(uploaded_by)
    if date_from:
        clauses.append("substr(a.uploaded_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(a.uploaded_at, 1, 10) <= ?")
        params.append(date_to)
    clauses.append("a.is_deleted = 0")
    where = " WHERE " + " AND ".join(clauses)
    sql = "SELECT a.* FROM attachments a" + where
    count = "SELECT COUNT(*) AS n FROM attachments a" + where
    return _run(db, sql, count, params, sort,
                {"date": "a.uploaded_at DESC, a.id",
                 "name": "a.original_filename, a.id",
                 "size": "a.size_bytes DESC, a.id",
                 "default": "a.uploaded_at DESC, a.id"}, page, page_size)


def search_own_tasks(db: DatabaseManager, *, employee_id: int, term: str = "",
                     page: int = 1,
                     page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    """Tasks assigned to one employee, name-matched. Used by global
    search for Staff, who may see only their own tasks (SRS §13.1)."""
    like = f"%{_escape_like(term.strip())}%" if term.strip() else "%"
    where = (" WHERE assigned_to = ? AND name LIKE ? ESCAPE '\\'")
    params: list = [employee_id, like]
    sql = "SELECT * FROM tasks" + where
    count = "SELECT COUNT(*) AS n FROM tasks" + where
    return _run(db, sql, count, params, "default",
                {"default": "due_at IS NULL, due_at, id"}, page, page_size)


def search_audit(db: DatabaseManager, *, employee_id: int | None = None,
                 action: str | None = None, entity_type: str | None = None,
                 entity_id: int | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 sort: str = "date", page: int = 1,
                 page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    clauses: list[str] = []
    params: list = []
    if employee_id is not None:
        clauses.append("employee_id = ?")
        params.append(employee_id)
    if action:
        clauses.append("action LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(action)}%")
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if date_from:
        clauses.append("substr(created_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(created_at, 1, 10) <= ?")
        params.append(date_to)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = "SELECT * FROM audit_logs" + where
    count = "SELECT COUNT(*) AS n FROM audit_logs" + where
    return _run(db, sql, count, params, sort,
                {"date": "created_at DESC, id",
                 "default": "created_at DESC, id"}, page, page_size)
