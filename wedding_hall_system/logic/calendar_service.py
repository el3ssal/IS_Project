"""Calendar (Phase 7, SRS §9, FR-057/FR-058).

Unified view for all halls + per-hall view, daily / weekly / monthly
as date ranges. Shows confirmed bookings, active Pending holds,
maintenance blocks and closures/special hours, with a detail panel
for a booking (price and money fields only when the caller holds
``booking.view_price`` / ``payment.view``).

Gradio has no native drag-and-drop calendar (GAP G-13), so the view
is a table of events plus a date/time edit action: changing a
booking's time from the calendar calls the SAME rescheduling
workflow as the Bookings screen (``BookingService.reschedule_booking``,
full revalidation, permissions and approval) — it never moves a
booking directly (SRS §9.2, FR-058, decision A-61).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import catalog_repository as catalog_repo
from ..data.repositories import customer_repository as customer_repo
from ..utils.exceptions import ValidationError
from ..utils.logging_config import get_logger
from .booking_service import BookingService
from .models import Employee
from .permissions import has_permission, require_permission

logger = get_logger(__name__)

RANGE_PRESETS: dict[str, int] = {"day": 1, "week": 7, "month": 30}


def _parse_day(value: str) -> date:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(
            "Date must be YYYY-MM-DD.") from None


class CalendarService:
    """Read-only calendar events + the reschedule action (FR-057/58)."""

    def __init__(self, db: DatabaseManager,
                 settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self.bookings = BookingService(db, settings)

    def _hall_name(self, hall_id: int | None) -> str:
        if hall_id is None:
            return "—"
        hall = catalog_repo.get_hall(self.db, hall_id)
        return hall["name"] if hall else f"Hall #{hall_id}"

    def _customer_name(self, employee, customer_id: int | None) -> str:
        if customer_id is None or not has_permission(
                self.db, employee, "customer.view"):
            return "—"
        row = customer_repo.get_customer(self.db, customer_id)
        return row["full_name"] if row else f"Customer #{customer_id}"

    def list_events(self, employee: Employee, *, date_from: str,
                    date_to: str, hall_id: int | None = None) -> list[dict]:
        """Events overlapping [date_from, date_to] (inclusive days).
        ``hall_id`` narrows to one hall; None is the unified view."""
        require_permission(self.db, employee, "booking.view")
        start_day = _parse_day(date_from)
        end_day = _parse_day(date_to)
        if end_day < start_day:
            raise ValidationError("End date must not be before start date.")
        if (end_day - start_day).days > 366:
            raise ValidationError("Calendar range is at most one year.")
        show_price = has_permission(self.db, employee, "booking.view_price")
        show_money = has_permission(self.db, employee, "payment.view")
        start_dt = datetime.combine(start_day, time.min)
        end_dt = datetime.combine(end_day + timedelta(days=1), time.min)
        events: list[dict] = []

        for booking in self.bookings.list_bookings(
                employee, hall_id=hall_id, start_from=start_dt,
                start_to=end_dt):
            events.append({
                "kind": "booking", "booking_id": booking.id,
                "number": booking.number,
                "title": (f"{booking.number} — "
                          f"{self._customer_name(employee, booking.customer_id)}"),
                "hall": self._hall_name(booking.hall_id),
                "start": booking.start_at.isoformat() if booking.start_at else "",
                "end": booking.end_at.isoformat() if booking.end_at else "",
                "status": booking.status,
                "guests": booking.guest_count,
                "total": booking.total_amount if show_price else None,
                "paid": booking.deposit_paid if show_money else None,
            })

        halls = ([catalog_repo.get_hall(self.db, hall_id)]
                 if hall_id is not None else
                 catalog_repo.list_halls(self.db, include_inactive=True))
        for hall in halls:
            if hall is None:
                continue
            hall_id_value = int(hall["id"])
            for block in catalog_repo.list_maintenance(self.db, hall_id_value):
                b_start = block["start_at"][:10]
                b_end = block["end_at"][:10]
                if b_end < date_from or b_start > date_to:
                    continue
                events.append({
                    "kind": "maintenance", "booking_id": None,
                    "number": f"MT-{block['id']}",
                    "title": f"Maintenance: {block['type']} — "
                             f"{block['description'] or ''}".strip(),
                    "hall": hall["name"], "start": block["start_at"],
                    "end": block["end_at"], "status": "maintenance",
                    "guests": None, "total": None, "paid": None,
                })
            for special in catalog_repo.list_special_hours(
                    self.db, hall_id_value):
                if not date_from <= special["date"] <= date_to:
                    continue
                if special["is_closed"]:
                    events.append({
                        "kind": "closure", "booking_id": None,
                        "number": f"CL-{special['id']}",
                        "title": f"Closed: {special['reason'] or 'Exceptional closure'}",
                        "hall": hall["name"],
                        "start": f"{special['date']}T00:00:00",
                        "end": f"{special['date']}T23:59:59",
                        "status": "closed", "guests": None,
                        "total": None, "paid": None,
                    })
                else:
                    events.append({
                        "kind": "special_hours", "booking_id": None,
                        "number": f"SH-{special['id']}",
                        "title": ("Special hours: "
                                  f"{special['open_time']}–{special['close_time']} "
                                  f"({special['reason'] or ''})").strip(),
                        "hall": hall["name"],
                        "start": (f"{special['date']}T{special['open_time']}:00"
                                  if special["open_time"] else
                                  f"{special['date']}T00:00:00"),
                        "end": (f"{special['date']}T{special['close_time']}:00"
                                if special["close_time"] else
                                f"{special['date']}T23:59:59"),
                        "status": "special hours", "guests": None,
                        "total": None, "paid": None,
                    })
        events.sort(key=lambda e: (e["start"], e["kind"]))
        return events

    def booking_detail(self, employee: Employee, booking_id: int) -> dict:
        """Detail panel for one booking (SRS §9.1): number, customer,
        hall, date/time, status, package, guests, price, paid,
        outstanding — money fields only with the matching permission."""
        booking = self.bookings.get_booking(employee, booking_id)
        if booking is None:
            raise ValidationError(f"Booking #{booking_id} does not exist.")
        show_price = has_permission(self.db, employee, "booking.view_price")
        show_money = has_permission(self.db, employee, "payment.view")
        package = ""
        if booking.package_id:
            row = catalog_repo.get_package(self.db, booking.package_id)
            package = row["name"] if row else ""
        event_type = ""
        if booking.event_type_id:
            row = catalog_repo.get_event_type(self.db, booking.event_type_id)
            event_type = row["name"] if row else ""
        payments = self.bookings.list_payments(employee, booking_id)
        collected = sum(p.amount for p in payments if p.status == "recorded")
        return {
            "booking_id": booking.id, "number": booking.number,
            "customer": self._customer_name(employee, booking.customer_id),
            "hall": self._hall_name(booking.hall_id),
            "event_type": event_type, "package": package,
            "start": booking.start_at.isoformat() if booking.start_at else "",
            "end": booking.end_at.isoformat() if booking.end_at else "",
            "status": booking.status, "guests": booking.guest_count,
            "total": booking.total_amount if show_price else None,
            "paid": collected if show_money else None,
            "outstanding": (booking.total_amount - collected
                            if show_money else None),
        }

    def reschedule_from_calendar(self, employee: Employee, booking_id: int, *,
                                 new_start_at: datetime, new_end_at: datetime,
                                 reason: str):
        """Move a booking from the calendar: the SAME workflow as the
        manual reschedule (full revalidation, ``booking.reschedule``
        permission, price recalculation) — never a direct move
        (SRS §9.2)."""
        return self.bookings.reschedule_booking(
            employee, booking_id, new_start_at=new_start_at,
            new_end_at=new_end_at, reason=reason)
