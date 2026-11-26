"""Quotation management (Phase 3, business layer — SRS §3.5, UC-02).

A quotation checks availability but reserves NOTHING. The itemised
price is frozen in ``quotation_items`` so later price changes never
rewrite a historical quotation. Statuses: Draft → Sent → Accepted →
(converted to a Pending booking by ``booking_service``) or Expired.
There is no customer-rejection workflow (SRS §3.5): a declined
quotation simply stays Draft/Sent or is never saved.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository as repo
from ..data.repositories import customer_repository as customer_repo
from ..utils.exceptions import BookingConflictError, NotFoundError, ValidationError
from ..utils.logging_config import get_logger
from . import audit_service
from .availability_service import AvailabilityService
from .models import Quotation, QuotationItem, ServiceSelection
from .permissions import require_permission
from .pricing_service import PricingService

logger = get_logger(__name__)

# Default quotation validity when the caller does not set one (decision A-21):
# 7 days from creation. The SRS requires a validity date/time but sets no
# default; 7 days is documented here, not hidden in the code.
DEFAULT_VALIDITY_DAYS = 7


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_quotation(row: dict) -> Quotation:
    return Quotation(
        id=row["id"], number=row["number"], customer_id=row["customer_id"],
        hall_id=row["hall_id"], event_type_id=row["event_type_id"],
        package_id=row["package_id"],
        start_at=_parse(row["start_at"]), end_at=_parse(row["end_at"]),
        guest_count=row["guest_count"], total_amount=row["total_amount"],
        status=row["status"], valid_until=_parse(row["valid_until"]),
        created_by=row["created_by"],
    )


class QuotationService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self.availability = AvailabilityService(db, settings)
        self.pricing = PricingService(db, settings)

    # -- helpers ------------------------------------------------------------
    def _next_number(self, conn=None) -> str:
        today = datetime.now().strftime("%Y%m%d")
        prefix = f"Q-{today}-"
        seq = repo.count_quotations_on_day(self.db, prefix, conn=conn) + 1
        return f"{prefix}{seq:04d}"

    def _get_or_raise(self, quotation_id: int, conn=None) -> dict:
        row = repo.get_quotation(self.db, quotation_id, conn=conn)
        if row is None:
            raise NotFoundError(f"Quotation #{quotation_id} does not exist.")
        return row

    # -- creation -----------------------------------------------------------
    def create_quotation(self, employee, *, customer_id: int, hall_id: int,
                         event_type_id: int | None = None,
                         package_id: int | None = None,
                         start_at: datetime, end_at: datetime, guests: int,
                         service_selections: list[ServiceSelection] | None = None,
                         discount: dict | None = None,
                         valid_until: datetime | None = None) -> Quotation:
        """Create a quotation (UC-02). Validates capacity, hours and
        CURRENT availability — but a quotation reserves nothing, so the
        same slot stays bookable afterwards. The price breakdown is
        frozen in ``quotation_items``."""
        require_permission(self.db, employee, "quotation.create")
        if customer_repo.get_customer(self.db, customer_id) is None:
            raise NotFoundError(f"Customer #{customer_id} does not exist.")
        if end_at <= start_at:
            raise ValidationError("End time must be after start time.")
        # Capacity + duration + hours (raises ValidationError/HallUnavailableError).
        hall_row = self._hall_or_raise(hall_id)
        from .models import Hall
        hall = Hall(**{k: v for k, v in hall_row.items() if k in Hall.__dataclass_fields__})
        duration_min = int((end_at - start_at).total_seconds() // 60)
        package_min = None
        if package_id is not None:
            package_min = self._package_min_duration(package_id)
        self.availability.validate_event_window(
            employee, hall=hall, start_at=start_at, end_at=end_at, guests=guests,
            package_min_duration_min=package_min, duration_min=duration_min)
        result = self.availability.check_availability(
            employee, hall_id=hall_id, start_at=start_at, end_at=end_at)
        if not result.available:
            raise BookingConflictError(
                f"Hall is not available for the requested time: {result.reason}")
        breakdown = self.pricing.calculate(
            employee, hall_id=hall_id, package_id=package_id,
            event_type_id=event_type_id, start_at=start_at,
            duration_min=duration_min, guests=guests,
            service_selections=service_selections, discount=discount)
        valid = valid_until or (datetime.now() + timedelta(days=DEFAULT_VALIDITY_DAYS))
        with self.db.transaction(immediate=True) as conn:
            number = self._next_number(conn=conn)
            quotation_id = repo.insert_quotation(
                self.db, number=number, customer_id=customer_id, hall_id=hall_id,
                event_type_id=event_type_id, package_id=package_id,
                start_at=_iso(start_at), end_at=_iso(end_at), guest_count=guests,
                total_amount=breakdown.total, valid_until=_iso(valid),
                created_by=employee.id, conn=conn)
            for item in breakdown.items:
                repo.insert_quotation_item(
                    self.db, quotation_id=quotation_id, kind=item.kind,
                    label=item.label, amount=item.amount, conn=conn)
            customer_repo.insert_interaction(
                self.db, customer_id=customer_id, employee_id=employee.id,
                type_="quotation",
                summary=f"Quotation {number} created for hall #{hall_id} "
                        f"({start_at.date()}), total {breakdown.total} piasters.",
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="quotation.created",
                entity_type="quotation", entity_id=quotation_id,
                new_value={"number": number, "total_amount": breakdown.total,
                           "status": "draft"},
                description=f"Quotation {number} created.", conn=conn)
        logger.info("Quotation %s created for customer #%s", number, customer_id)
        return self.get_quotation(employee, quotation_id)

    def _hall_or_raise(self, hall_id: int) -> dict:
        from ..data.repositories import catalog_repository as catalog_repo
        row = catalog_repo.get_hall(self.db, hall_id)
        if row is None:
            raise NotFoundError(f"Hall #{hall_id} does not exist.")
        return row

    def _package_min_duration(self, package_id: int) -> int | None:
        from ..data.repositories import catalog_repository as catalog_repo
        row = catalog_repo.get_package(self.db, package_id)
        if row is None:
            raise NotFoundError(f"Package #{package_id} does not exist.")
        return int(row["min_duration_min"])

    # -- lifecycle ------------------------------------------------------------
    def get_quotation(self, employee, quotation_id: int) -> Quotation | None:
        require_permission(self.db, employee, "quotation.view")
        row = repo.get_quotation(self.db, quotation_id)
        return _row_to_quotation(row) if row else None

    def list_quotations(self, employee, *, customer_id: int | None = None,
                        status: str | None = None) -> list[Quotation]:
        require_permission(self.db, employee, "quotation.view")
        return [_row_to_quotation(row) for row in repo.list_quotations(
            self.db, customer_id=customer_id, status=status)]

    def list_items(self, employee, quotation_id: int) -> list[QuotationItem]:
        require_permission(self.db, employee, "quotation.view")
        self._get_or_raise(quotation_id)
        return [QuotationItem(
            id=r["id"], quotation_id=r["quotation_id"], kind=r["kind"],
            label=r["label"], amount=r["amount"])
            for r in repo.list_quotation_items(self.db, quotation_id)]

    def _set_status(self, employee, quotation_id: int, old: str, new: str) -> Quotation:
        with self.db.transaction() as conn:
            repo.update_quotation_status(self.db, quotation_id, new, conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="quotation.status_changed",
                entity_type="quotation", entity_id=quotation_id,
                old_value={"status": old}, new_value={"status": new},
                description=f"Quotation #{quotation_id}: {old} → {new}.", conn=conn)
        updated = self.get_quotation(employee, quotation_id)
        assert updated is not None
        return updated

    def send_quotation(self, employee, quotation_id: int) -> Quotation:
        """Draft → Sent. An expired quotation cannot be sent — it is
        marked Expired and the caller must create a new one (SRS §3.5)."""
        require_permission(self.db, employee, "quotation.create")
        row = self._get_or_raise(quotation_id)
        if row["status"] != "draft":
            raise ValidationError(
                f"Only a Draft quotation can be sent (current status: {row['status']}).")
        if _row_to_quotation(row).is_expired_by_time:
            self._set_status(employee, quotation_id, "draft", "expired")
            raise ValidationError(
                "This quotation has expired. Create a new quotation with current rules.")
        return self._set_status(employee, quotation_id, "draft", "sent")

    def accept_quotation(self, employee, quotation_id: int) -> Quotation:
        """Draft/Sent → Accepted. An expired quotation cannot be
        accepted — it is marked Expired and the caller must create a
        new quotation with current rules (SRS §3.5)."""
        require_permission(self.db, employee, "quotation.create")
        row = self._get_or_raise(quotation_id)
        if row["status"] not in ("draft", "sent"):
            raise ValidationError(
                f"Only a Draft or Sent quotation can be accepted "
                f"(current status: {row['status']}).")
        quotation = _row_to_quotation(row)
        if quotation.is_expired_by_time:
            self._set_status(employee, quotation_id, row["status"], "expired")
            raise ValidationError(
                "This quotation has expired. Create a new quotation with current rules.")
        return self._set_status(employee, quotation_id, row["status"], "accepted")

    def expire_quotation(self, employee, quotation_id: int) -> Quotation:
        """Mark a Draft/Sent quotation Expired (manual expiry)."""
        require_permission(self.db, employee, "quotation.create")
        row = self._get_or_raise(quotation_id)
        if row["status"] not in ("draft", "sent"):
            raise ValidationError(
                f"Only a Draft or Sent quotation can be expired "
                f"(current status: {row['status']}).")
        return self._set_status(employee, quotation_id, row["status"], "expired")

    def sweep_expired_quotations(self, employee) -> list[int]:
        """Mark Draft/Sent quotations whose validity passed as Expired.
        Lazy expiry (no background process, GAP G-11): run on reads."""
        require_permission(self.db, employee, "quotation.view")
        now_iso = _iso(datetime.now())
        expired: list[int] = []
        with self.db.transaction(immediate=True) as conn:
            rows = conn.execute(
                """SELECT id, status FROM quotations
                   WHERE status IN ('draft', 'sent') AND valid_until IS NOT NULL
                     AND valid_until <= ?""",
                (now_iso,),
            ).fetchall()
            for row in rows:
                repo.update_quotation_status(
                    self.db, int(row["id"]), "expired", conn=conn)
                audit_service.record(
                    self.db, employee_id=None, action="quotation.expired",
                    entity_type="quotation", entity_id=int(row["id"]),
                    old_value={"status": row["status"]},
                    new_value={"status": "expired"},
                    description="Quotation validity passed.", conn=conn)
                expired.append(int(row["id"]))
        return expired
