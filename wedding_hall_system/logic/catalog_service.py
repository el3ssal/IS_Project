"""Package / service / pricing-rule management (Phase 2, business layer —
SRS §3.6–§3.7).

Every write requires ``catalog.manage`` (Owner/Manager); reads require
``catalog.view``. Deactivation never deletes history: packages, services
and rules are soft-deactivated, never hard-deleted.
"""
from __future__ import annotations

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import catalog_repository as repo
from ..utils.exceptions import NotFoundError, ValidationError
from ..utils.logging_config import get_logger
from ..utils.validators import validate_amount_piasters, validate_positive_int
from . import audit_service
from .models import Package, PricingRule, Service
from .permissions import require_permission

logger = get_logger(__name__)


def _get_package_or_raise(db: DatabaseManager, package_id: int) -> dict:
    row = repo.get_package(db, package_id)
    if row is None:
        raise NotFoundError(f"Package #{package_id} does not exist.")
    return row


def _get_service_or_raise(db: DatabaseManager, service_id: int) -> dict:
    row = repo.get_service(db, service_id)
    if row is None:
        raise NotFoundError(f"Service #{service_id} does not exist.")
    return row


class CatalogService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- packages -----------------------------------------------------------
    def list_packages(self, employee, *, include_inactive: bool = True) -> list[Package]:
        require_permission(self.db, employee, "catalog.view")
        return [Package(**{k: v for k, v in row.items() if k in Package.__dataclass_fields__})
                for row in repo.list_packages(self.db, include_inactive=include_inactive)]

    def get_package(self, employee, package_id: int) -> Package:
        require_permission(self.db, employee, "catalog.view")
        row = _get_package_or_raise(self.db, package_id)
        return Package(**{k: v for k, v in row.items() if k in Package.__dataclass_fields__})

    def create_package(self, employee, *, name: str, description: str = "",
                       base_price: int, default_duration_min: int = 120,
                       min_duration_min: int = 60, included_guests: int = 0,
                       extra_guest_price: int = 0,
                       hall_ids: list[int] | None = None,
                       event_type_ids: list[int] | None = None,
                       service_ids: list[int] | None = None) -> Package:
        require_permission(self.db, employee, "catalog.manage")
        package = Package(
            name=name, description=description,
            base_price=validate_amount_piasters(base_price, "Base price"),
            default_duration_min=default_duration_min,
            min_duration_min=min_duration_min, included_guests=included_guests,
            extra_guest_price=validate_amount_piasters(extra_guest_price, "Extra guest price"),
            hall_ids=hall_ids or [], event_type_ids=event_type_ids or [],
            service_ids=service_ids or [],
        )
        if any(p["name"] == package.name for p in repo.list_packages(self.db)):
            raise ValidationError(f"A package named '{package.name}' already exists.")
        self._check_link_ids(package.hall_ids, package.event_type_ids, package.service_ids)
        with self.db.transaction() as conn:
            package_id = repo.insert_package(
                self.db, name=package.name, description=package.description,
                base_price=package.base_price,
                default_duration_min=package.default_duration_min,
                min_duration_min=package.min_duration_min,
                included_guests=package.included_guests,
                extra_guest_price=package.extra_guest_price,
            )
            repo.set_package_halls(self.db, package_id, package.hall_ids)
            repo.set_package_event_types(self.db, package_id, package.event_type_ids)
            repo.set_package_services(self.db, package_id, package.service_ids)
            audit_service.record(
                self.db, employee_id=employee.id, action="package.created",
                entity_type="package", entity_id=package_id,
                new_value={"name": package.name},
                description=f"Package '{package.name}' created.", conn=conn,
            )
        package.id = package_id
        logger.info("Package created: id=%s name=%s", package_id, package.name)
        return package

    def update_package(self, employee, package_id: int, **fields) -> Package:
        require_permission(self.db, employee, "catalog.manage")
        old = _get_package_or_raise(self.db, package_id)
        link_fields = {k: fields.pop(k) for k in
                       ("hall_ids", "event_type_ids", "service_ids") if k in fields}
        merged = {**old, **{k: v for k, v in fields.items() if v is not None}}
        package = Package(**{k: v for k, v in merged.items()
                             if k in Package.__dataclass_fields__})
        if package.name != old["name"] and any(
                p["name"] == package.name for p in repo.list_packages(self.db)):
            raise ValidationError(f"A package named '{package.name}' already exists.")
        hall_ids = link_fields.get("hall_ids", old["hall_ids"])
        event_type_ids = link_fields.get("event_type_ids", old["event_type_ids"])
        service_ids = link_fields.get("service_ids", old["service_ids"])
        self._check_link_ids(hall_ids, event_type_ids, service_ids)
        with self.db.transaction() as conn:
            repo.update_package(
                self.db, package_id, name=package.name,
                description=package.description, base_price=package.base_price,
                default_duration_min=package.default_duration_min,
                min_duration_min=package.min_duration_min,
                included_guests=package.included_guests,
                extra_guest_price=package.extra_guest_price,
                is_active=package.is_active,
            )
            repo.set_package_halls(self.db, package_id, hall_ids)
            repo.set_package_event_types(self.db, package_id, event_type_ids)
            repo.set_package_services(self.db, package_id, service_ids)
            audit_service.record(
                self.db, employee_id=employee.id, action="package.updated",
                entity_type="package", entity_id=package_id,
                old_value={"name": old["name"]},
                new_value={"name": package.name},
                description=f"Package '{package.name}' updated.", conn=conn,
            )
        package.hall_ids, package.event_type_ids, package.service_ids = (
            hall_ids, event_type_ids, service_ids)
        return package

    def _check_link_ids(self, hall_ids, event_type_ids, service_ids) -> None:
        for hall_id in hall_ids:
            if repo.get_hall(self.db, hall_id) is None:
                raise ValidationError(f"Hall #{hall_id} does not exist.")
        for event_type_id in event_type_ids:
            if repo.get_event_type(self.db, event_type_id) is None:
                raise ValidationError(f"Event type #{event_type_id} does not exist.")
        for service_id in service_ids:
            if repo.get_service(self.db, service_id) is None:
                raise ValidationError(f"Service #{service_id} does not exist.")

    # -- services -----------------------------------------------------------
    def list_services(self, employee, *, include_inactive: bool = True) -> list[Service]:
        require_permission(self.db, employee, "catalog.view")
        return [Service(**{k: v for k, v in row.items() if k in Service.__dataclass_fields__})
                for row in repo.list_services(self.db, include_inactive=include_inactive)]

    def get_service(self, employee, service_id: int) -> Service:
        require_permission(self.db, employee, "catalog.view")
        row = _get_service_or_raise(self.db, service_id)
        return Service(**{k: v for k, v in row.items() if k in Service.__dataclass_fields__})

    def create_service(self, employee, *, name: str, description: str = "",
                       pricing_method: str = "per_event", unit_price: int,
                       hall_ids: list[int] | None = None) -> Service:
        require_permission(self.db, employee, "catalog.manage")
        service = Service(
            name=name, description=description, pricing_method=pricing_method,
            unit_price=validate_amount_piasters(unit_price, "Unit price"),
            hall_ids=hall_ids or [],
        )
        if any(s["name"] == service.name for s in repo.list_services(self.db)):
            raise ValidationError(f"A service named '{service.name}' already exists.")
        for hall_id in service.hall_ids:
            if repo.get_hall(self.db, hall_id) is None:
                raise ValidationError(f"Hall #{hall_id} does not exist.")
        with self.db.transaction() as conn:
            service_id = repo.insert_service(
                self.db, name=service.name, description=service.description,
                pricing_method=service.pricing_method, unit_price=service.unit_price,
            )
            repo.set_service_halls(self.db, service_id, service.hall_ids)
            audit_service.record(
                self.db, employee_id=employee.id, action="service.created",
                entity_type="service", entity_id=service_id,
                new_value={"name": service.name},
                description=f"Service '{service.name}' created.", conn=conn,
            )
        service.id = service_id
        return service

    def update_service(self, employee, service_id: int, **fields) -> Service:
        require_permission(self.db, employee, "catalog.manage")
        old = _get_service_or_raise(self.db, service_id)
        hall_ids = fields.pop("hall_ids", old["hall_ids"]) if "hall_ids" in fields else old["hall_ids"]
        merged = {**old, **{k: v for k, v in fields.items() if v is not None}}
        service = Service(**{k: v for k, v in merged.items()
                             if k in Service.__dataclass_fields__})
        if service.name != old["name"] and any(
                s["name"] == service.name for s in repo.list_services(self.db)):
            raise ValidationError(f"A service named '{service.name}' already exists.")
        for hall_id in hall_ids:
            if repo.get_hall(self.db, hall_id) is None:
                raise ValidationError(f"Hall #{hall_id} does not exist.")
        with self.db.transaction() as conn:
            repo.update_service(
                self.db, service_id, name=service.name,
                description=service.description,
                pricing_method=service.pricing_method,
                unit_price=service.unit_price, is_active=service.is_active,
            )
            repo.set_service_halls(self.db, service_id, hall_ids)
            audit_service.record(
                self.db, employee_id=employee.id, action="service.updated",
                entity_type="service", entity_id=service_id,
                old_value={"name": old["name"]},
                new_value={"name": service.name},
                description=f"Service '{service.name}' updated.", conn=conn,
            )
        service.hall_ids = hall_ids
        return service

    def compatible_services(self, employee, *, hall_id: int | None = None,
                            package_id: int | None = None) -> list[Service]:
        """Services usable for a hall/package (SRS §3.6).

        A service linked to specific halls (service_halls rows) is shown
        only for those halls; a service with no links works everywhere.
        Package-included services are listed first.
        """
        require_permission(self.db, employee, "catalog.view")
        services = self.list_services(employee, include_inactive=False)
        included: set[int] = set()
        if package_id is not None:
            included = set(_get_package_or_raise(self.db, package_id)["service_ids"])
        result = []
        for service in services:
            if hall_id is not None and service.hall_ids and hall_id not in service.hall_ids:
                continue
            result.append(service)
        result.sort(key=lambda s: (s.id not in included, s.name))
        return result

    # -- pricing rules --------------------------------------------------------
    def list_pricing_rules(self, employee, *, include_inactive: bool = True) -> list[PricingRule]:
        require_permission(self.db, employee, "catalog.view")
        return [PricingRule(**{k: v for k, v in row.items()
                               if k in PricingRule.__dataclass_fields__})
                for row in repo.list_pricing_rules(self.db, include_inactive=include_inactive)]

    def create_pricing_rule(self, employee, *, name: str, hall_id: int | None = None,
                            package_id: int | None = None,
                            event_type_id: int | None = None,
                            weekday: int | None = None, season: str = "",
                            adjustment_type: str = "percent",
                            adjustment_value: int) -> PricingRule:
        require_permission(self.db, employee, "catalog.manage")
        rule = PricingRule(
            name=name, hall_id=hall_id, package_id=package_id,
            event_type_id=event_type_id, weekday=weekday, season=season,
            adjustment_type=adjustment_type, adjustment_value=adjustment_value,
        )
        if rule.hall_id is not None and repo.get_hall(self.db, rule.hall_id) is None:
            raise ValidationError(f"Hall #{rule.hall_id} does not exist.")
        if rule.package_id is not None and repo.get_package(self.db, rule.package_id) is None:
            raise ValidationError(f"Package #{rule.package_id} does not exist.")
        if rule.event_type_id is not None and repo.get_event_type(self.db, rule.event_type_id) is None:
            raise ValidationError(f"Event type #{rule.event_type_id} does not exist.")
        rule.id = repo.insert_pricing_rule(
            self.db, name=rule.name, hall_id=rule.hall_id,
            package_id=rule.package_id, event_type_id=rule.event_type_id,
            weekday=rule.weekday, season=rule.season,
            adjustment_type=rule.adjustment_type,
            adjustment_value=rule.adjustment_value,
        )
        return rule

    def update_pricing_rule(self, employee, rule_id: int, **fields) -> PricingRule:
        require_permission(self.db, employee, "catalog.manage")
        row = repo.get_pricing_rule(self.db, rule_id)
        if row is None:
            raise NotFoundError(f"Pricing rule #{rule_id} does not exist.")
        merged = {**row, **{k: v for k, v in fields.items() if v is not None}}
        rule = PricingRule(**{k: v for k, v in merged.items()
                              if k in PricingRule.__dataclass_fields__})
        repo.update_pricing_rule(
            self.db, rule_id, name=rule.name, hall_id=rule.hall_id,
            package_id=rule.package_id, event_type_id=rule.event_type_id,
            weekday=rule.weekday, season=rule.season,
            adjustment_type=rule.adjustment_type,
            adjustment_value=rule.adjustment_value, is_active=rule.is_active,
        )
        return rule
