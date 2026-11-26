"""In-app notifications (Phase 4, SRS §10).

In-app only — no email/SMS/WhatsApp. Notifications target one employee,
one role, or both; role-targeted rows are visible to every holder of that
role. There is no per-user configuration (SRS §10): the Owner configures
which roles receive each notification type and with what priority, stored
as JSON in the ``notification.config`` system setting (seeded from
``config/defaults.py`` DEFAULT_NOTIFICATION_CONFIG).

``notify`` is an internal helper used by other services after their own
permission checks — it performs no permission check itself. The
``NotificationService`` class owns the inbox (read/mark-read/config).
"""
from __future__ import annotations

import json

from ..config.defaults import DEFAULT_NOTIFICATION_CONFIG
from ..data.database import DatabaseManager
from ..data.repositories import employee_repository
from ..data.repositories import notification_repository as repo
from ..utils.exceptions import ValidationError
from ..utils.logging_config import get_logger
from .models import Employee, Notification
from .permissions import has_permission, require_permission

logger = get_logger(__name__)

CONFIG_KEY = "notification.config"


# ------------------------------------------------------------------ internal


def _role_id(db: DatabaseManager, role_name: str) -> int | None:
    return employee_repository.get_role_id(db, role_name)


def get_notification_config(db: DatabaseManager) -> dict:
    """Owner-configured type -> {roles, priority} mapping (seeded defaults
    when the setting is missing or unparsable)."""
    raw = ""
    try:
        from ..data.repositories import system_repository

        raw = system_repository.get_setting(db, CONFIG_KEY, "")
    except Exception:  # pragma: no cover - settings read is best-effort
        raw = ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            logger.warning("notification.config is not valid JSON; using defaults.")
    return json.loads(json.dumps(DEFAULT_NOTIFICATION_CONFIG))


def notify(db: DatabaseManager, *, type: str, title: str, body: str = "",
           priority: str | None = None, entity_type: str = "",
           entity_id: int | None = None,
           recipient_employee_id: int | None = None,
           recipient_role_id: int | None = None, conn=None) -> list[int]:
    """Create notification row(s) and return their ids. When neither
    recipient is given, recipients resolve from the Owner-configured
    mapping for the type; when the type has no mapping at all, the
    notification goes to the Owner role so it is never silently dropped.
    Unknown types raise ValidationError (via the Notification model)."""
    Notification(type=type, title=title)  # validates type/priority early
    config = get_notification_config(db)
    role_ids: list[int] = []
    if recipient_role_id is not None:
        role_ids.append(recipient_role_id)
    elif recipient_employee_id is None:
        entry = config.get(type, {}) if isinstance(config, dict) else {}
        roles = entry.get("roles", []) if isinstance(entry, dict) else []
        for role_name in roles:
            rid = _role_id(db, str(role_name))
            if rid is not None:
                role_ids.append(rid)
        if not role_ids:
            owner_id = _role_id(db, "Owner")
            if owner_id is not None:
                role_ids.append(owner_id)
    if priority is None:
        entry = config.get(type, {}) if isinstance(config, dict) else {}
        priority = entry.get("priority", "normal") if isinstance(entry, dict) else "normal"
    created: list[int] = []
    if recipient_employee_id is not None:
        created.append(repo.insert_notification(
            db, recipient_role_id=None, recipient_employee_id=recipient_employee_id,
            type=type, title=title, body=body, priority=priority,
            entity_type=entity_type, entity_id=entity_id, conn=conn))
    for rid in role_ids:
        created.append(repo.insert_notification(
            db, recipient_role_id=rid, recipient_employee_id=None,
            type=type, title=title, body=body, priority=priority,
            entity_type=entity_type, entity_id=entity_id, conn=conn))
    return created


# -------------------------------------------------------------------- inbox


class NotificationService:
    """Inbox operations for the logged-in employee."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def _role_ids(self, employee: Employee) -> list[int]:
        return employee_repository.get_role_ids(self.db, employee.id)

    def inbox(self, employee: Employee, *, unread_only: bool = False) -> list[dict]:
        require_permission(self.db, employee, "notification.view")
        return repo.list_inbox(self.db, employee_id=int(employee.id),
                               role_ids=self._role_ids(employee),
                               unread_only=unread_only)

    def unread_count(self, employee: Employee) -> int:
        require_permission(self.db, employee, "notification.view")
        return repo.unread_count(self.db, employee_id=int(employee.id),
                                role_ids=self._role_ids(employee))

    def mark_read(self, employee: Employee, notification_id: int) -> None:
        require_permission(self.db, employee, "notification.view")
        row = repo.get_notification(self.db, notification_id)
        if row is None:
            from ..utils.exceptions import NotFoundError

            raise NotFoundError(f"Notification #{notification_id} does not exist.")
        visible = (
            row["recipient_employee_id"] == employee.id
            or row["recipient_role_id"] in self._role_ids(employee)
        )
        if not visible:
            from ..utils.exceptions import AuthorizationError

            raise AuthorizationError("This notification is not addressed to you.")
        repo.mark_read(self.db, notification_id)

    def mark_all_read(self, employee: Employee) -> int:
        """Mark every unread notification in this employee's inbox as
        read. Returns how many were marked."""
        require_permission(self.db, employee, "notification.view")
        unread = self.inbox(employee, unread_only=True)
        for row in unread:
            repo.mark_read(self.db, int(row["id"]))
        return len(unread)

    def get_config(self, employee: Employee) -> dict:
        require_permission(self.db, employee, "settings.view")
        return get_notification_config(self.db)

    def update_config(self, employee: Employee, config: dict) -> dict:
        """Owner updates the type -> {roles, priority} mapping. Every type
        must be a known notification type, every role must exist, every
        priority must be a known priority."""
        from ..data.repositories import system_repository

        require_permission(self.db, employee, "settings.manage")
        if not isinstance(config, dict):
            raise ValidationError("Notification config must be an object.")
        from .models import NOTIFICATION_PRIORITIES, NOTIFICATION_TYPES

        for type_name, entry in config.items():
            if type_name not in NOTIFICATION_TYPES:
                raise ValidationError(f"Unknown notification type: {type_name!r}.")
            if not isinstance(entry, dict):
                raise ValidationError(f"Config for {type_name!r} must be an object.")
            for role_name in entry.get("roles", []):
                if _role_id(self.db, str(role_name)) is None:
                    raise ValidationError(f"Unknown role in notification config: {role_name!r}.")
            if entry.get("priority", "normal") not in NOTIFICATION_PRIORITIES:
                raise ValidationError(
                    f"Invalid priority for {type_name!r}: {entry.get('priority')!r}.")
        system_repository.set_setting(
            self.db, CONFIG_KEY, json.dumps(config, ensure_ascii=False))
        return config

    @staticmethod
    def has_notification_view(db: DatabaseManager, employee: Employee) -> bool:
        return has_permission(db, employee, "notification.view")
