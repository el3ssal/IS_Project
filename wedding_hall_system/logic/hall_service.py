"""Hall management: halls, weekly/special hours, maintenance, event types
(Phase 2, business layer — SRS §4.1–§4.4).

Every write requires ``hall.manage`` (Owner/Manager); reads require
``hall.view``. Deactivation never deletes history (SRS §4.1, BR-23):
halls and event types are soft-deactivated, never hard-deleted.
"""
from __future__ import annotations

from datetime import datetime

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import catalog_repository as repo
from ..utils.exceptions import (
    HallDeleteBlockedError,
    NotFoundError,
    ValidationError,
)
from ..utils.logging_config import get_logger
from ..utils.validators import (
    require_text,
    validate_amount_piasters,
    validate_date_yyyy_mm_dd,
    validate_positive_int,
    validate_time_hhmm,
    validate_weekday,
)
from . import audit_service
from .models import EventType, Hall, HallSpecialHours, HallWeeklyHours, MaintenanceBlock
from .notification_service import notify
from .permissions import require_permission

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_hall_or_raise(db: DatabaseManager, hall_id: int) -> dict:
    hall = repo.get_hall(db, hall_id)
    if hall is None:
        raise NotFoundError(f"Hall #{hall_id} does not exist.")
    return hall


class HallService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- halls ------------------------------------------------------------
    def list_halls(self, employee, *, include_inactive: bool = True) -> list[Hall]:
        require_permission(self.db, employee, "hall.view")
        return [Hall(**{k: v for k, v in row.items() if k in Hall.__dataclass_fields__})
                for row in repo.list_halls(self.db, include_inactive=include_inactive)]

    def get_hall(self, employee, hall_id: int) -> Hall:
        require_permission(self.db, employee, "hall.view")
        row = _get_hall_or_raise(self.db, hall_id)
        return Hall(**{k: v for k, v in row.items() if k in Hall.__dataclass_fields__})

    def create_hall(self, employee, *, name: str, description: str = "",
                    capacity: int, base_price: int, status: str = "active",
                    facilities: list[str] | None = None, notes: str = "") -> Hall:
        require_permission(self.db, employee, "hall.manage")
        hall = Hall(name=name, description=description, capacity=capacity,
                    base_price=validate_amount_piasters(base_price, "Base price"),
                    status=status, facilities=facilities or [], notes=notes)
        if repo.get_hall_by_name(self.db, hall.name) is not None:
            raise ValidationError(f"A hall named '{hall.name}' already exists.")
        with self.db.transaction() as conn:
            hall_id = repo.insert_hall(
                self.db, name=hall.name, description=hall.description,
                capacity=hall.capacity, base_price=hall.base_price,
                status=hall.status, facilities_json=hall.facilities_json(),
                notes=hall.notes, conn=conn,
            )
            audit_service.record(
                self.db, employee_id=employee.id, action="hall.created",
                entity_type="hall", entity_id=hall_id,
                new_value={"name": hall.name, "capacity": hall.capacity},
                description=f"Hall '{hall.name}' created.", conn=conn,
            )
        hall.id = hall_id
        logger.info("Hall created: id=%s name=%s", hall_id, hall.name)
        return hall

    def update_hall(self, employee, hall_id: int, **fields) -> Hall:
        require_permission(self.db, employee, "hall.manage")
        old = _get_hall_or_raise(self.db, hall_id)
        merged = {**old, **{k: v for k, v in fields.items() if v is not None}}
        # A-92: when the form changes the descriptive status directly
        # (without touching the flag), keep the flag in sync the same
        # way: "inactive" deactivates, "active" activates;
        # "maintenance" leaves the flag alone. set_hall_active passes
        # both explicitly, so this never overrides its
        # preserved-maintenance rule.
        if (fields.get("status") is not None
                and fields.get("is_active") is None):
            if fields["status"] == "inactive":
                merged["is_active"] = False
            elif fields["status"] == "active":
                merged["is_active"] = True
        hall = Hall(**{k: v for k, v in merged.items() if k in Hall.__dataclass_fields__})
        if hall.name != old["name"] and repo.get_hall_by_name(self.db, hall.name) is not None:
            raise ValidationError(f"A hall named '{hall.name}' already exists.")
        with self.db.transaction() as conn:
            repo.update_hall(
                self.db, hall_id, name=hall.name, description=hall.description,
                capacity=hall.capacity, base_price=hall.base_price,
                status=hall.status, facilities_json=hall.facilities_json(),
                notes=hall.notes, is_active=hall.is_active,
            )
            audit_service.record(
                self.db, employee_id=employee.id, action="hall.updated",
                entity_type="hall", entity_id=hall_id,
                old_value={"name": old["name"], "status": old["status"]},
                new_value={"name": hall.name, "status": hall.status},
                description=f"Hall '{hall.name}' updated.", conn=conn,
            )
        return hall

    def set_hall_active(self, employee, hall_id: int, is_active: bool) -> Hall:
        """Deactivate/reactivate. Historical bookings are never deleted."""
        require_permission(self.db, employee, "hall.manage")
        old = _get_hall_or_raise(self.db, hall_id)
        # A-92: keep the descriptive status in sync with the
        # soft-delete flag, so the single الحالة column never lies:
        # deactivating an "active" hall marks it "inactive";
        # reactivating an "inactive" hall marks it "active". A hall
        # under "maintenance" keeps that status either way — it is
        # still not bookable (is_bookable needs status == "active"
        # AND is_active).
        if is_active:
            status = "active" if old["status"] == "inactive" else old["status"]
        else:
            status = "inactive" if old["status"] == "active" else old["status"]
        hall = self.update_hall(employee, hall_id, is_active=is_active,
                                status=status)
        logger.info("Hall id=%s active=%s status=%s (was %s/%s)", hall_id,
                    is_active, status, old["is_active"], old["status"])
        return hall

    def delete_hall(self, employee, hall_id: int) -> str:
        """Permanently delete a hall (A-90).

        Requires ``hall.delete`` (Owner/Manager). Blocked — with an
        error carrying the counts, so the UI can render the block in
        the user's language — when the hall has business/financial
        records that must be preserved: any booking, any quotation,
        any reschedule-history row naming it (old or new hall), or
        any hall-scoped expense. Hall-owned configuration (weekly and
        special hours, maintenance blocks, package/service links,
        hall-scoped pricing rules) has no meaning without the hall
        and is removed with it, in the same transaction. The
        deletion is audited with the hall's name and capacity.
        Returns the deleted hall's name.
        """
        require_permission(self.db, employee, "hall.delete")
        hall = _get_hall_or_raise(self.db, hall_id)
        counts = self.db.query_one(
            """SELECT
                   (SELECT COUNT(*) FROM bookings WHERE hall_id = ?) AS bookings,
                   (SELECT COUNT(*) FROM quotations WHERE hall_id = ?) AS quotations,
                   (SELECT COUNT(*) FROM reschedule_history
                     WHERE old_hall_id = ? OR new_hall_id = ?) AS history,
                   (SELECT COUNT(*) FROM expenses
                     WHERE scope_type = 'hall' AND scope_id = ?) AS expenses""",
            (hall_id, hall_id, hall_id, hall_id, hall_id))
        n_book = int(counts["bookings"])
        n_quot = int(counts["quotations"])
        n_hist = int(counts["history"])
        n_exp = int(counts["expenses"])
        if n_book or n_quot or n_hist or n_exp:
            raise HallDeleteBlockedError(
                hall["name"], bookings=n_book, quotations=n_quot,
                history=n_hist, expenses=n_exp)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM hall_weekly_hours WHERE hall_id = ?",
                         (hall_id,))
            conn.execute("DELETE FROM hall_special_hours WHERE hall_id = ?",
                         (hall_id,))
            conn.execute("DELETE FROM maintenance_blocks WHERE hall_id = ?",
                         (hall_id,))
            conn.execute("DELETE FROM package_halls WHERE hall_id = ?",
                         (hall_id,))
            conn.execute("DELETE FROM service_halls WHERE hall_id = ?",
                         (hall_id,))
            conn.execute("DELETE FROM pricing_rules WHERE hall_id = ?",
                         (hall_id,))
            conn.execute("DELETE FROM halls WHERE id = ?", (hall_id,))
            audit_service.record(
                self.db, employee_id=employee.id, action="hall.deleted",
                entity_type="hall", entity_id=hall_id,
                old_value={"name": hall["name"],
                           "capacity": hall["capacity"]},
                description=f"Hall '{hall['name']}' permanently deleted.",
                conn=conn,
            )
        logger.info("Hall deleted: id=%s name=%s", hall_id, hall["name"])
        return hall["name"]

    # -- weekly hours ------------------------------------------------------
    def list_weekly_hours(self, employee, hall_id: int) -> list[HallWeeklyHours]:
        require_permission(self.db, employee, "hall.view")
        _get_hall_or_raise(self.db, hall_id)
        return [HallWeeklyHours(**row) for row in repo.list_weekly_hours(self.db, hall_id)]

    def set_weekly_hours(self, employee, hall_id: int, *, weekday: int,
                         open_time: str, close_time: str,
                         is_closed: bool = False) -> HallWeeklyHours:
        require_permission(self.db, employee, "hall.manage")
        _get_hall_or_raise(self.db, hall_id)
        hours = HallWeeklyHours(
            hall_id=hall_id, weekday=validate_weekday(weekday),
            open_time=open_time, close_time=close_time, is_closed=is_closed,
        )
        with self.db.transaction() as conn:
            hours_id = repo.set_weekly_hours(
                self.db, hall_id, hours.weekday,
                hours.open_time if not hours.is_closed else "00:00",
                hours.close_time if not hours.is_closed else "00:00",
                hours.is_closed,
            )
            audit_service.record(
                self.db, employee_id=employee.id, action="hall.hours_set",
                entity_type="hall", entity_id=hall_id,
                new_value={"weekday": hours.weekday, "closed": hours.is_closed},
                description=f"Weekly hours set for hall #{hall_id}.", conn=conn,
            )
        hours.id = hours_id
        return hours

    def delete_weekly_hours(self, employee, hours_id: int) -> None:
        require_permission(self.db, employee, "hall.manage")
        repo.delete_weekly_hours(self.db, hours_id)

    # -- special hours -----------------------------------------------------
    def list_special_hours(self, employee, hall_id: int) -> list[HallSpecialHours]:
        require_permission(self.db, employee, "hall.view")
        _get_hall_or_raise(self.db, hall_id)
        return [HallSpecialHours(**row) for row in repo.list_special_hours(self.db, hall_id)]

    def add_special_hours(self, employee, hall_id: int, *, date: str,
                          open_time: str | None, close_time: str | None,
                          is_closed: bool = False, reason: str = "") -> HallSpecialHours:
        require_permission(self.db, employee, "hall.manage")
        _get_hall_or_raise(self.db, hall_id)
        special = HallSpecialHours(
            hall_id=hall_id, date=validate_date_yyyy_mm_dd(date),
            open_time=open_time, close_time=close_time,
            is_closed=is_closed, reason=reason,
        )
        if repo.get_special_hours(self.db, hall_id, special.date) is not None:
            raise ValidationError(f"Hall already has special hours for {special.date}.")
        special.id = repo.insert_special_hours(
            self.db, hall_id=hall_id, date=special.date,
            open_time=special.open_time, close_time=special.close_time,
            is_closed=special.is_closed, reason=special.reason,
        )
        return special

    def delete_special_hours(self, employee, special_id: int) -> None:
        require_permission(self.db, employee, "hall.manage")
        repo.delete_special_hours(self.db, special_id)

    # -- maintenance --------------------------------------------------------
    def list_maintenance(self, employee, hall_id: int | None = None) -> list[MaintenanceBlock]:
        require_permission(self.db, employee, "hall.view")
        rows = repo.list_maintenance(self.db, hall_id)
        return [MaintenanceBlock(
            id=r["id"], hall_id=r["hall_id"], type=r["type"],
            description=r["description"],
            start_at=datetime.fromisoformat(r["start_at"]),
            end_at=datetime.fromisoformat(r["end_at"]),
            recorded_by=r["recorded_by"],
        ) for r in rows]

    def add_maintenance(self, employee, hall_id: int, *, type_: str,
                        description: str = "", start_at: datetime,
                        end_at: datetime) -> MaintenanceBlock:
        require_permission(self.db, employee, "hall.manage")
        _get_hall_or_raise(self.db, hall_id)
        block = MaintenanceBlock(
            hall_id=hall_id, type=type_, description=description,
            start_at=start_at, end_at=end_at, recorded_by=employee.id,
        )
        block.id = repo.insert_maintenance(
            self.db, hall_id=hall_id, type_=block.type,
            description=block.description,
            start_at=block.start_at.isoformat(timespec="seconds"),
            end_at=block.end_at.isoformat(timespec="seconds"),
            recorded_by=block.recorded_by,
        )
        # FR-060 (Phase 6): a new maintenance block notifies the Manager
        # role (notification.config maps hall_maintenance -> Manager).
        hall = repo.get_hall(self.db, hall_id)
        notify(self.db, type="hall_maintenance",
               title=f"Maintenance scheduled — {hall['name'] if hall else f'hall #{hall_id}'}",
               body=f"{block.type} maintenance "
                    f"{block.start_at.isoformat(timespec='minutes')} to "
                    f"{block.end_at.isoformat(timespec='minutes')}"
                    + (f" — {block.description}" if block.description else ""),
               title_key="notif.hall_maintenance.title",
               body_key="notif.hall_maintenance.body",
               data={"hall_name": hall["name"] if hall else f"hall #{hall_id}",
                     "type": block.type,
                     "start": block.start_at.isoformat(timespec="minutes"),
                     "end": block.end_at.isoformat(timespec="minutes")},
               entity_type="maintenance_block", entity_id=block.id)
        logger.info("Maintenance block id=%s hall=%s", block.id, hall_id)
        return block

    def delete_maintenance(self, employee, block_id: int) -> None:
        require_permission(self.db, employee, "hall.manage")
        repo.delete_maintenance(self.db, block_id)

    # -- event types --------------------------------------------------------
    def list_event_types(self, employee, *, include_inactive: bool = True) -> list[EventType]:
        require_permission(self.db, employee, "catalog.view")
        return [EventType(**row) for row in repo.list_event_types(
            self.db, include_inactive=include_inactive)]

    def create_event_type(self, employee, *, name: str,
                          default_duration_min: int = 120) -> EventType:
        require_permission(self.db, employee, "catalog.manage")
        event_type = EventType(name=name, default_duration_min=default_duration_min)
        if any(t["name"] == event_type.name
               for t in repo.list_event_types(self.db)):
            raise ValidationError(f"An event type named '{event_type.name}' already exists.")
        event_type.id = repo.insert_event_type(
            self.db, name=event_type.name,
            default_duration_min=event_type.default_duration_min,
        )
        return event_type

    def update_event_type(self, employee, event_type_id: int, **fields) -> EventType:
        require_permission(self.db, employee, "catalog.manage")
        row = repo.get_event_type(self.db, event_type_id)
        if row is None:
            raise NotFoundError(f"Event type #{event_type_id} does not exist.")
        merged = {**row, **{k: v for k, v in fields.items() if v is not None}}
        event_type = EventType(**{k: v for k, v in merged.items()
                                  if k in EventType.__dataclass_fields__})
        repo.update_event_type(
            self.db, event_type_id, name=event_type.name,
            default_duration_min=event_type.default_duration_min,
            is_active=event_type.is_active,
        )
        return event_type
