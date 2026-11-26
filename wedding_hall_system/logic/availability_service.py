"""Hall availability (Phase 2, business layer — SRS §3.2, §4.2, §9.3).

Pure availability logic used by quotations (Phase 3), booking creation
(Phase 3) and rescheduling. Buffer policy (GAP A-06, decision A-18):
the EVENT itself must fit inside opening hours; the buffer (before and
after, from settings) must not overlap another booking, a maintenance
block or a closure. Half-open intervals [start, end) throughout.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import catalog_repository as repo
from ..data.repositories import system_repository
from ..utils.exceptions import HallUnavailableError, ValidationError
from ..utils.logging_config import get_logger
from .models import Hall
from .permissions import require_permission

logger = get_logger(__name__)


@dataclass
class AvailabilityResult:
    available: bool
    reason: str = ""  # empty when available; a human-readable reason otherwise

    def __bool__(self) -> bool:
        return self.available


def _to_minutes(time_hhmm: str) -> int:
    hours, minutes = time_hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _buffer_minutes(db: DatabaseManager, settings: AppSettings) -> int:
    try:
        return int(system_repository.get_setting(
            db, "booking.buffer_minutes", str(settings.buffer_minutes)))
    except ValueError:
        return settings.buffer_minutes


def _min_duration_minutes(db: DatabaseManager, settings: AppSettings) -> int:
    try:
        return int(system_repository.get_setting(
            db, "booking.min_duration_minutes", "120"))
    except ValueError:
        return 120


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def expire_overdue_holds(db: DatabaseManager, now: datetime | None = None,
                         conn=None) -> list[int]:
    """Lazy hold expiry (SRS §3.4, GAP G-11): Pending bookings whose
    hold + grace period has passed without a valid deposit become
    Expired and release their time. Nothing in the approved stack runs
    in the background, so expiry happens on access — every booking
    creation, booking read and availability check runs this first.

    Writes the expiry into booking_history and the audit log on the
    same connection/transaction as the caller (pass ``conn`` to join
    the caller's transaction; otherwise opens its own BEGIN IMMEDIATE
    transaction). Returns the expired booking ids. The actor is the
    system itself, so employee_id is None.
    """
    from . import audit_service

    moment = now or datetime.now()
    now_iso = _iso(moment)
    if conn is not None:
        rows = conn.execute(
            """SELECT * FROM bookings
               WHERE status = 'pending' AND grace_expires_at IS NOT NULL
                 AND grace_expires_at <= ?
               ORDER BY grace_expires_at""",
            (now_iso,),
        ).fetchall()
        expired: list[int] = []
        for row in rows:
            booking_id = int(row["id"])
            conn.execute(
                """UPDATE bookings SET status = 'expired',
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (booking_id,),
            )
            conn.execute(
                """INSERT INTO booking_history
                   (booking_id, employee_id, action, old_value, new_value, reason)
                   VALUES (?, NULL, 'expired', 'pending', 'expired',
                           'Hold and grace period expired without a valid deposit.')""",
                (booking_id,),
            )
            audit_service.record(
                db, employee_id=None, action="booking.expired",
                entity_type="booking", entity_id=booking_id,
                old_value={"status": "pending"}, new_value={"status": "expired"},
                description="Hold and grace period expired without a valid deposit.",
                conn=conn,
            )
            expired.append(booking_id)
        return expired
    with db.transaction(immediate=True) as own_conn:
        return expire_overdue_holds(db, now=moment, conn=own_conn)


class AvailabilityService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- main check ---------------------------------------------------------
    def check_availability(self, employee, *, hall_id: int,
                           start_at: datetime, end_at: datetime,
                           exclude_booking_id: int | None = None,
                           conn=None) -> AvailabilityResult:
        """Is the hall free for [start_at, end_at)? Checks, in order:
        hall exists/active, opening hours (special hours override weekly),
        maintenance blocks (with buffer), active bookings (with buffer),
        closures. Returns a result with a reason — never raises for a
        plain "unavailable" answer.

        Pass ``conn`` to run the check on the caller's transaction
        connection (booking creation re-checks inside its BEGIN IMMEDIATE
        transaction, SRS §3.2). Without ``conn`` the check first sweeps
        overdue holds (lazy expiry, GAP G-11) so a stale hold never
        blocks a slot; inside a transaction the caller sweeps first.
        """
        require_permission(self.db, employee, "hall.view")
        if end_at <= start_at:
            return AvailabilityResult(False, "End time must be after start time.")
        if conn is None:
            try:
                expire_overdue_holds(self.db)
            except Exception:  # pragma: no cover - sweep is best-effort on reads
                logger.warning("Hold-expiry sweep failed; continuing availability check.")
        hall_row = repo.get_hall(self.db, hall_id, conn=conn)
        if hall_row is None:
            return AvailabilityResult(False, f"Hall #{hall_id} does not exist.")
        hall = Hall(**{k: v for k, v in hall_row.items() if k in Hall.__dataclass_fields__})
        if not hall.is_bookable:
            state = "inactive" if not hall.is_active else hall.status
            return AvailabilityResult(False, f"Hall '{hall.name}' is {state} and cannot be booked.")

        hours = self._intervals_for_date(hall_id, start_at.date(), conn=conn)
        if hours is None:
            return AvailabilityResult(False, "The hall is closed on that day (exceptional closure).")
        if not hours:
            return AvailabilityResult(False, "The hall is closed on that day.")
        if end_at.date() != start_at.date():
            return AvailabilityResult(False, "Events must start and end on the same day.")
        start_min = start_at.hour * 60 + start_at.minute
        end_min = end_at.hour * 60 + end_at.minute
        fits = any(open_min <= start_min and end_min <= close_min
                   for open_min, close_min in hours)
        if not fits:
            spans = ", ".join(
                f"{o // 60:02d}:{o % 60:02d}–{c // 60:02d}:{c % 60:02d}"
                for o, c in hours)
            return AvailabilityResult(
                False,
                f"Outside opening hours ({spans}). The event must fit inside one opening interval.",
            )

        buffer_min = _buffer_minutes(self.db, self.settings)
        buffered_start = start_at - timedelta(minutes=buffer_min)
        buffered_end = end_at + timedelta(minutes=buffer_min)
        blocked_start = buffered_start.isoformat(timespec="seconds")
        blocked_end = buffered_end.isoformat(timespec="seconds")
        blocks = repo.overlapping_maintenance(
            self.db, hall_id, blocked_start, blocked_end, conn=conn)
        if blocks:
            block = blocks[0]
            return AvailabilityResult(
                False,
                f"Maintenance '{block['type']}' blocks the hall "
                f"({block['start_at']} – {block['end_at']}).",
            )
        bookings = repo.overlapping_bookings(
            self.db, hall_id, blocked_start, blocked_end,
            exclude_booking_id=exclude_booking_id, conn=conn,
        )
        if bookings:
            booking = bookings[0]
            return AvailabilityResult(
                False,
                f"Conflicts with {booking['status']} booking "
                f"({booking['start_at']} – {booking['end_at']}), including buffer time.",
            )
        return AvailabilityResult(True)

    def _intervals_for_date(self, hall_id: int, day, conn=None) -> list[tuple[int, int]] | None:
        """Opening intervals in minutes for one date.

        Special hours override weekly hours for that date. Returns None
        when the date is an exceptional closure, [] when there are no
        opening hours that day, otherwise the list of (open, close)
        intervals — an event must fit inside one of them.
        """
        special = repo.get_special_hours(self.db, hall_id, day.isoformat(), conn=conn)
        if special is not None:
            if special["is_closed"]:
                return None
            return [(_to_minutes(special["open_time"]), _to_minutes(special["close_time"]))]
        rows = [r for r in repo.list_weekly_hours(self.db, hall_id, conn=conn)
                if r["weekday"] == day.weekday() and not r["is_closed"]]
        return [(_to_minutes(r["open_time"]), _to_minutes(r["close_time"])) for r in rows]

    def validate_event_window(self, employee, *, hall: Hall,
                              start_at: datetime, end_at: datetime,
                              guests: int, package_min_duration_min: int | None = None,
                              duration_min: int | None = None) -> int:
        """Validate duration/capacity for an event. Returns the duration in
        minutes. Raises ValidationError / HallUnavailableError.

        Duration must be >= the package minimum (or the venue minimum when
        no package applies). Guests must not exceed hall capacity.
        Availability itself is checked separately by check_availability.
        """
        require_permission(self.db, employee, "hall.view")
        if end_at <= start_at:
            raise ValidationError("End time must be after start time.")
        minutes = int((end_at - start_at).total_seconds() // 60)
        if duration_min is not None and duration_min != minutes:
            raise ValidationError("Given duration does not match start/end times.")
        minimum = (package_min_duration_min
                   if package_min_duration_min is not None
                   else _min_duration_minutes(self.db, self.settings))
        if minutes < minimum:
            raise ValidationError(
                f"Event duration ({minutes} min) is below the minimum of {minimum} min.")
        if guests < 1:
            raise ValidationError("Guest count must be at least 1.")
        if guests > hall.capacity:
            raise ValidationError(
                f"Guest count ({guests}) exceeds hall capacity ({hall.capacity}).")
        if not hall.is_bookable:
            raise HallUnavailableError(f"Hall '{hall.name}' cannot be booked.")
        return minutes
