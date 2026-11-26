"""Advanced + global search (Phase 6, SRS §13.1).

Every module search requires that module's view permission, and every
result is filtered to what the caller may see — a user without
``booking.view_price`` never gets amounts, a user without
``customer.view_sensitive`` never gets a National ID (it is never
included in any search result at all), and a Staff user only ever sees
their own tasks. Sorting is restricted to the repository allowlists;
pagination is page/page_size with a total count.
"""
from __future__ import annotations

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..data.repositories import search_repository as repo
from ..utils.exceptions import AuthorizationError, ValidationError
from ..utils.logging_config import get_logger
from .models import ATTACHMENT_CATEGORIES
from .permissions import has_permission, require_permission

logger = get_logger(__name__)

BOOKING_STATUSES = ("pending", "confirmed", "in_progress", "completed",
                    "cancelled", "no_show", "expired")
CONTRACT_SIGNATURE_STATUSES = ("draft", "pending_signature",
                               "partially_signed", "signed", "cancelled")
PAYMENT_METHODS = ("cash", "bank_transfer", "visa_card", "instapay", "other")


def _mask_national_id(value: str | None) -> str:
    """Search results never expose a full National ID (SRS §13.1)."""
    value = (value or "").strip()
    if len(value) <= 4:
        return "****" if value else ""
    return "****" + value[-4:]


class SearchService:
    """Module search + one-box global search, both RBAC-filtered."""

    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- module search -------------------------------------------------------
    def search_customers(self, employee, *, term: str = "", sort: str = "name",
                         page: int = 1, page_size: int = 20) -> dict:
        require_permission(self.db, employee, "customer.view")
        result = repo.search_customers(self.db, term=term, sort=sort,
                                       page=page, page_size=page_size)
        result["rows"] = [{
            "id": r["id"], "full_name": r["full_name"], "phone": r["phone"],
            "email": r["email"], "national_id": _mask_national_id(r["national_id"]),
        } for r in result["rows"]]
        return result

    def search_bookings(self, employee, *, term: str = "",
                        hall_id: int | None = None, status: str | None = None,
                        date_from: str | None = None, date_to: str | None = None,
                        sort: str = "date", page: int = 1,
                        page_size: int = 20) -> dict:
        require_permission(self.db, employee, "booking.view")
        if status is not None and status not in BOOKING_STATUSES:
            raise ValidationError(f"Unknown booking status {status!r}.")
        show_price = has_permission(self.db, employee, "booking.view_price")
        result = repo.search_bookings(
            self.db, term=term, hall_id=hall_id, status=status,
            date_from=date_from, date_to=date_to, sort=sort,
            page=page, page_size=page_size)
        result["rows"] = [{
            "id": r["id"], "number": r["number"],
            "customer": r.get("customer_name") or "",
            "hall_id": r["hall_id"], "status": r["status"],
            "start_at": r["start_at"], "end_at": r["end_at"],
            "guest_count": r["guest_count"],
            "total_amount": (int(r["total_amount"]) if show_price else None),
            "total_amount_display": (
                format_money(int(r["total_amount"]), self.settings.currency)
                if show_price else "—"),
        } for r in result["rows"]]
        return result

    def search_contracts(self, employee, *, term: str = "",
                         signature_status: str | None = None,
                         sort: str = "number", page: int = 1,
                         page_size: int = 20) -> dict:
        require_permission(self.db, employee, "contract.view")
        if (signature_status is not None
                and signature_status not in CONTRACT_SIGNATURE_STATUSES):
            raise ValidationError(
                f"Unknown signature status {signature_status!r}.")
        result = repo.search_contracts(
            self.db, term=term, signature_status=signature_status,
            sort=sort, page=page, page_size=page_size)
        result["rows"] = [{
            "id": r["id"], "number": r["number"],
            "booking_number": r.get("booking_number") or "",
            "customer": r.get("customer_name") or "",
            "template_name": r["template_name"],
            "signature_status": r["signature_status"],
            "signature_date": r["signature_date"] or "",
        } for r in result["rows"]]
        return result

    def search_payments(self, employee, *, term: str = "",
                        method: str | None = None,
                        date_from: str | None = None, date_to: str | None = None,
                        amount_min: int | None = None,
                        amount_max: int | None = None,
                        sort: str = "date", page: int = 1,
                        page_size: int = 20) -> dict:
        require_permission(self.db, employee, "payment.view")
        if method is not None and method not in PAYMENT_METHODS:
            raise ValidationError(f"Unknown payment method {method!r}.")
        result = repo.search_payments(
            self.db, term=term, method=method, date_from=date_from,
            date_to=date_to, amount_min=amount_min, amount_max=amount_max,
            sort=sort, page=page, page_size=page_size)
        result["rows"] = [{
            "id": r["id"], "number": r["number"],
            "booking_number": r.get("booking_number") or "",
            "method": r["method"], "status": r["status"],
            "amount": int(r["amount"]),
            "amount_display": format_money(int(r["amount"]),
                                           self.settings.currency),
            "confirmed_at": r["confirmed_at"] or "",
        } for r in result["rows"]]
        return result

    def search_staff(self, employee, *, term: str = "",
                     specialty: str | None = None, status: str | None = None,
                     sort: str = "name", page: int = 1,
                     page_size: int = 20) -> dict:
        """Staff search needs ``task.view`` (Manager) or ``user.manage``
        (Owner/Manager) — a Receptionist or Accountant has neither and
        cannot list employees."""
        if not (has_permission(self.db, employee, "task.view")
                or has_permission(self.db, employee, "user.manage")):
            raise AuthorizationError(
                "You do not have permission to search staff.")
        if status is not None and status not in ("active", "inactive"):
            raise ValidationError(f"Unknown staff status {status!r}.")
        result = repo.search_staff(
            self.db, term=term, specialty=specialty, status=status,
            sort=sort, page=page, page_size=page_size)
        result["rows"] = [{
            "id": r["id"], "username": r["username"],
            "full_name": r["full_name"], "phone": r["phone"],
            "email": r["email"], "specialties": r["specialties"] or "",
            "is_active": bool(r["is_active"]),
        } for r in result["rows"]]
        return result

    def search_files(self, employee, *, term: str = "",
                     category: str | None = None,
                     entity_type: str | None = None,
                     entity_id: int | None = None,
                     uploaded_by: int | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     sort: str = "date", page: int = 1,
                     page_size: int = 20) -> dict:
        """File search (SRS §11/§13.1). Rows are filtered to entities
        the caller may view — the same rule as AttachmentService.list."""
        require_permission(self.db, employee, "attachment.download")
        if category is not None and category not in ATTACHMENT_CATEGORIES:
            raise ValidationError(f"Unknown file category {category!r}.")
        from .attachment_service import ATTACHABLE_ENTITIES, _can_view_entity

        if entity_type is not None and entity_type not in ATTACHABLE_ENTITIES:
            raise ValidationError(f"Unknown entity type {entity_type!r}.")
        # RBAC at the SQL level, so the total and the pages match what
        # the caller may see (SRS §13.1).
        allowed = [t for t in ATTACHABLE_ENTITIES
                   if _can_view_entity(self.db, employee, t)]
        if entity_type is not None and entity_type not in allowed:
            return {"rows": [], "total": 0, "page": max(1, page),
                    "page_size": page_size}
        result = repo.search_files(
            self.db, term=term, category=category, entity_type=entity_type,
            entity_types=None if entity_type else allowed,
            entity_id=entity_id, uploaded_by=uploaded_by,
            date_from=date_from, date_to=date_to, sort=sort,
            page=page, page_size=page_size)
        result["rows"] = [{
            "id": r["id"], "original_filename": r["original_filename"],
            "mime_type": r["mime_type"], "size_bytes": r["size_bytes"],
            "category": r["category"], "entity_type": r["entity_type"],
            "entity_id": r["entity_id"], "uploaded_by": r["uploaded_by"],
            "uploaded_at": r["uploaded_at"],
        } for r in result["rows"]]
        return result

    # -- global search ---------------------------------------------------------
    def global_search(self, employee, query: str, *, page: int = 1,
                      page_size: int = 10) -> dict:
        """One box across customers, bookings, contracts, payments,
        staff and events (SRS §13.1). A module appears only when the
        caller holds its view permission; snippets never carry amounts
        the caller may not see, National IDs, or another employee's
        tasks. Staff see only their own tasks under ``tasks``."""
        query = (query or "").strip()
        if not query:
            raise ValidationError("Enter a search term first.")
        out: dict[str, dict] = {}
        if has_permission(self.db, employee, "customer.view"):
            out["customers"] = self.search_customers(
                employee, term=query, page=page, page_size=page_size)
        if has_permission(self.db, employee, "booking.view"):
            out["bookings"] = self.search_bookings(
                employee, term=query, page=page, page_size=page_size)
            # "Events" are bookings viewed by event: same rows, keyed
            # separately so the UI can show an Events section.
            out["events"] = out["bookings"]
        if has_permission(self.db, employee, "contract.view"):
            out["contracts"] = self.search_contracts(
                employee, term=query, page=page, page_size=page_size)
        if has_permission(self.db, employee, "payment.view"):
            out["payments"] = self.search_payments(
                employee, term=query, page=page, page_size=page_size)
        if (has_permission(self.db, employee, "task.view")
                or has_permission(self.db, employee, "user.manage")):
            out["staff"] = self.search_staff(
                employee, term=query, page=page, page_size=page_size)
        if has_permission(self.db, employee, "attachment.download"):
            out["files"] = self.search_files(
                employee, term=query, page=page, page_size=page_size)
        # Staff (and anyone with only task.view_assigned) see their own
        # tasks, nothing else from the task side.
        if not out.get("staff") and has_permission(
                self.db, employee, "task.view_assigned"):
            result = repo.search_own_tasks(
                self.db, employee_id=int(employee.id), term=query,
                page=page, page_size=page_size)
            result["rows"] = [{
                "id": r["id"], "name": r["name"], "status": r["status"],
                "priority": r["priority"], "due_at": r["due_at"] or "",
                "booking_id": r["booking_id"],
            } for r in result["rows"]]
            out["tasks"] = result
        return out
