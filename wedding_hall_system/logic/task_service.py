"""Event plans and tasks (Phase 4, SRS §7.2–§7.4).

An event plan is auto-generated for a booking from the matching task
template (by event type, then package): one plan per booking, one task
per template item, due = event start minus the item's offset.
Generation copies template items into ``tasks`` rows, so a template
change (or deletion) never affects plans already generated (SRS §7.2)
— and ``event_plans`` carries no template link for that reason.

Managers assign tasks; a staff conflict (unavailable, or already
assigned during the task's window) produces a WARNING that is recorded
in the audit log and returned — it never blocks the assignment
(SRS §7.4). Assigned staff update their own tasks' status (pending →
in_progress → completed; completed is final — the SRS has no
task-cancellation workflow and the schema allows only these three
statuses). Every assignment and status change is written to
``task_history`` and raises a notification.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..data.database import DatabaseManager
from ..data.repositories import (
    booking_repository,
    employee_repository,
    notification_repository as notif_repo,
    operations_repository as ops_repo,
)
from ..utils.exceptions import NotFoundError, ValidationError
from ..utils.logging_config import get_logger
from ..utils.validators import require_text
from . import audit_service
from .models import TASK_PRIORITIES, TASK_STATUSES, Task
from .notification_service import notify
from .permissions import has_permission, require_permission

logger = get_logger(__name__)

# Status transitions (SRS §7.3). Managers may use any of them; staff may
# use them only for their own assigned tasks. A completed task is final.
_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("in_progress", "completed"),
    "in_progress": ("completed",),
    "completed": (),
}


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class TaskService:
    """Task templates, event-plan generation, assignment, status."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    # -- task templates ----------------------------------------------------
    def list_templates(self, employee) -> list[dict]:
        require_permission(self.db, employee, "task.view")
        return [{**t, "items": ops_repo.list_template_items(self.db, int(t["id"]))}
                for t in ops_repo.list_templates(self.db)]

    def get_template(self, employee, template_id: int) -> dict:
        require_permission(self.db, employee, "task.view")
        row = ops_repo.get_template(self.db, template_id)
        if row is None:
            raise NotFoundError(f"Task template #{template_id} does not exist.")
        return {**row, "items": ops_repo.list_template_items(self.db, template_id)}

    def create_template(self, employee, *, name: str, event_type_id: int | None,
                        package_id: int | None, items: list[dict]) -> dict:
        require_permission(self.db, employee, "task.manage")
        require_text(name, "Template name", max_length=200)
        self._validate_template_items(items)
        with self.db.transaction() as conn:
            template_id = ops_repo.insert_template(
                self.db, name=name.strip(), event_type_id=event_type_id,
                package_id=package_id, conn=conn)
            for item in items:
                ops_repo.insert_template_item(
                    self.db, template_id=template_id, name=item["name"].strip(),
                    description=item.get("description", ""),
                    default_due_offset_min=int(item["due_offset_min"]),
                    priority=item.get("priority", "medium"), conn=conn)
        audit_service.record(
            self.db, employee_id=employee.id, action="task.template_create",
            entity_type="task_template", entity_id=template_id,
            new_value={"name": name.strip()},
            description=f"Task template {name!r} created "
                        f"({len(items)} item(s)).")
        return self.get_template(employee, template_id)

    @staticmethod
    def _validate_template_items(items: list[dict]) -> None:
        if not items:
            raise ValidationError("A task template needs at least one item.")
        for item in items:
            require_text(item.get("name", ""), "Task item name", max_length=200)
            if int(item.get("due_offset_min", -1)) < 0:
                raise ValidationError(
                    "Task item due offset must be 0 or more minutes before the "
                    "event start.")
            if item.get("priority", "medium") not in TASK_PRIORITIES:
                raise ValidationError(
                    f"Task priority must be one of {TASK_PRIORITIES}.")

    def update_template(self, employee, template_id: int, *,
                        name: str | None = None,
                        event_type_id: int | None = None,
                        package_id: int | None = None,
                        items: list[dict] | None = None) -> dict:
        require_permission(self.db, employee, "task.manage")
        if ops_repo.get_template(self.db, template_id) is None:
            raise NotFoundError(f"Task template #{template_id} does not exist.")
        if items is not None:
            self._validate_template_items(items)
        if name is not None:
            require_text(name, "Template name", max_length=200)
        with self.db.transaction() as conn:
            ops_repo.update_template(
                self.db, template_id, name=name.strip() if name is not None else None,
                event_type_id=event_type_id, package_id=package_id, conn=conn)
            if items is not None:
                ops_repo.delete_template_items(self.db, template_id, conn=conn)
                for item in items:
                    ops_repo.insert_template_item(
                        self.db, template_id=template_id,
                        name=item["name"].strip(),
                        description=item.get("description", ""),
                        default_due_offset_min=int(item["due_offset_min"]),
                        priority=item.get("priority", "medium"), conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="task.template_update",
                entity_type="task_template", entity_id=template_id,
                description=f"Task template #{template_id} updated.",
                conn=conn)
        return self.get_template(employee, template_id)

    def delete_template(self, employee, template_id: int) -> None:
        require_permission(self.db, employee, "task.manage")
        template = ops_repo.get_template(self.db, template_id)
        if template is None:
            raise NotFoundError(f"Task template #{template_id} does not exist.")
        # No in-use check: generation copies items into tasks, so
        # deleting a template never affects existing event plans (SRS §7.2).
        ops_repo.delete_template(self.db, template_id)
        audit_service.record(
            self.db, employee_id=employee.id, action="task.template_delete",
            entity_type="task_template", entity_id=template_id,
            description=f"Task template {template['name']!r} deleted.")

    # -- event plans --------------------------------------------------------
    def generate_event_plan(self, employee, booking_id: int,
                            template_id: int | None = None) -> dict:
        """Generate the event plan for a Confirmed booking from the
        matching task template (SRS §7.2). One plan per booking."""
        require_permission(self.db, employee, "task.manage")
        booking = booking_repository.get_booking(self.db, booking_id)
        if booking is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        if booking["status"] not in ("confirmed", "in_progress", "completed"):
            raise ValidationError(
                "Event plans can only be generated for Confirmed bookings "
                f"(current status: {booking['status']}).")
        if ops_repo.get_event_plan(self.db, booking_id) is not None:
            raise ValidationError(
                "This booking already has an event plan.")
        if template_id is None:
            template_id = self._find_template(booking)
            if template_id is None:
                raise ValidationError(
                    "No task template matches this booking's event type or "
                    "package — create one first, or pick a template.")
        template = ops_repo.get_template(self.db, template_id)
        if template is None:
            raise NotFoundError(f"Task template #{template_id} does not exist.")
        items = ops_repo.list_template_items(self.db, template_id)
        if not items:
            raise ValidationError(
                f"Task template {template['name']!r} has no items.")
        start = _parse(booking["start_at"])
        with self.db.transaction() as conn:
            plan_id = ops_repo.insert_event_plan(self.db, booking_id, conn=conn)
            tasks: list[dict] = []
            for item in items:
                due = start - timedelta(minutes=int(item["default_due_offset_min"])) \
                    if start else None
                task_id = ops_repo.insert_task(
                    self.db, event_plan_id=plan_id, booking_id=booking_id,
                    service_id=None, name=item["name"],
                    description=item["description"], assigned_to=None,
                    priority=item["priority"],
                    due_at=due.isoformat(timespec="seconds") if due else None,
                    conn=conn)
                tasks.append({"id": task_id, "name": item["name"],
                              "due_at": due.isoformat(timespec="seconds")
                              if due else "",
                              "priority": item["priority"],
                              "status": "pending"})
            audit_service.record(
                self.db, employee_id=employee.id, action="event_plan.generate",
                entity_type="event_plan", entity_id=plan_id,
                new_value={"booking_id": booking_id,
                           "template_id": template_id,
                           "task_count": len(tasks)},
                description=f"Event plan generated for booking "
                            f"{booking['number']} from template "
                            f"{template['name']!r} ({len(tasks)} task(s)).",
                conn=conn)
        return {"id": plan_id, "booking_id": booking_id,
                "template_id": template_id,
                "template_name": template["name"], "tasks": tasks}

    def _find_template(self, booking: dict) -> int | None:
        """Best match for a booking: a template matching both event type
        and package wins; then one matching the event type alone; then a
        generic template (no event type, no package)."""
        templates = ops_repo.list_templates(self.db)
        event_type_id = booking.get("event_type_id")
        package_id = booking.get("package_id")
        for row in templates:
            if (row["event_type_id"] == event_type_id
                    and row["package_id"] == package_id):
                return int(row["id"])
        for row in templates:
            if row["event_type_id"] == event_type_id and row["package_id"] is None:
                return int(row["id"])
        for row in templates:
            if row["event_type_id"] is None and row["package_id"] is None:
                return int(row["id"])
        return None

    def get_event_plan(self, employee, booking_id: int) -> dict:
        require_permission(self.db, employee, "task.view")
        plan = ops_repo.get_event_plan(self.db, booking_id)
        if plan is None:
            raise NotFoundError(f"Booking #{booking_id} has no event plan.")
        tasks = ops_repo.list_tasks(self.db, event_plan_id=int(plan["id"]))
        return {"id": int(plan["id"]), "booking_id": booking_id, "tasks": tasks}

    # -- assignment ---------------------------------------------------------
    def _conflict_warnings(self, employee_id: int, task: dict,
                           exclude_task_id: int | None = None) -> list[str]:
        """Availability + overlap checks (SRS §7.4). Returns warning
        strings; an empty list means no conflict."""
        warnings: list[str] = []
        target_date = _parse(task["due_at"]).date().isoformat() if task.get("due_at") else None
        if target_date:
            for entry in ops_repo.list_availability(
                    self.db, employee_id=employee_id, date=target_date):
                if entry["kind"] == "unavailable":
                    warnings.append(
                        f"Employee is marked unavailable on {target_date}"
                        + (f" ({entry['note']})" if entry["note"] else "")
                        + ".")
                elif entry["kind"] == "time_off":
                    warnings.append(
                        f"Employee has time off on {target_date}"
                        + (f" ({entry['note']})" if entry["note"] else "")
                        + ".")
        # Overlap: another task assigned to the same person, due within
        # 30 minutes of this task's due time.
        due = _parse(task["due_at"]) if task.get("due_at") else None
        if due:
            for other in ops_repo.list_tasks(self.db, assigned_to=employee_id):
                if other["status"] == "completed":
                    continue
                if exclude_task_id is not None and int(other["id"]) == exclude_task_id:
                    continue
                other_due = _parse(other["due_at"]) if other.get("due_at") else None
                if other_due and abs((other_due - due).total_seconds()) <= 1800:
                    warnings.append(
                        f"Employee already has task #{other['id']} "
                        f"({other['name']}) due {other['due_at']} — "
                        "overlapping assignment.")
        return warnings

    def assign_task(self, employee, task_id: int, assigned_to: int,
                    *, due_at: str | None = None) -> dict:
        """Assign a task (Manager). Conflicts produce a recorded warning
        and are returned — the assignment still succeeds (SRS §7.4)."""
        require_permission(self.db, employee, "task.manage")
        task = ops_repo.get_task(self.db, task_id)
        if task is None:
            raise NotFoundError(f"Task #{task_id} does not exist.")
        if task["status"] == "completed":
            raise ValidationError("A completed task cannot be assigned.")
        if employee_repository.get_by_id(self.db, assigned_to) is None:
            raise NotFoundError(f"Employee #{assigned_to} does not exist.")
        warnings = self._conflict_warnings(assigned_to, task, exclude_task_id=task_id)
        with self.db.transaction() as conn:
            ops_repo.update_task(
                self.db, task_id, assigned_to=assigned_to, due_at=due_at, conn=conn)
            ops_repo.insert_task_history(
                self.db, task_id=task_id, employee_id=employee.id,
                action="assigned", old_value=str(task["assigned_to"] or ""),
                new_value=str(assigned_to), conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="task.assign",
                entity_type="task", entity_id=task_id,
                old_value={"assigned_to": task["assigned_to"]},
                new_value={"assigned_to": assigned_to},
                description=f"Task #{task_id} assigned to employee "
                            f"#{assigned_to}."
                            + (f" WARNINGS: {'; '.join(warnings)}"
                               if warnings else ""),
                conn=conn)
            notify(self.db, type="task_assigned",
                   recipient_employee_id=assigned_to,
                   title=f"Task assigned: {task['name']}",
                   body=f"You were assigned '{task['name']}'"
                        + (f" (due {task['due_at']})" if task["due_at"] else "")
                        + (f" Warning: {'; '.join(warnings)}" if warnings else ""),
                   entity_type="task", entity_id=task_id, conn=conn)
            if warnings:
                notify(self.db, type="assignment_conflict",
                       title=f"Assignment conflict on task #{task_id}",
                       body=f"Task #{task_id} assigned to employee "
                            f"#{assigned_to} with conflict(s): {'; '.join(warnings)}",
                       entity_type="task", entity_id=task_id, conn=conn)
        result = dict(ops_repo.get_task(self.db, task_id))
        result["warnings"] = warnings
        return result

    # -- status ---------------------------------------------------------------
    def update_task_status(self, employee, task_id: int, status: str,
                           note: str = "") -> Task:
        """Status update by the assignee or a Manager. ``note`` is kept
        for the caller's context only — task_history has no note column,
        so it is recorded in the audit description instead."""
        task = ops_repo.get_task(self.db, task_id)
        if task is None:
            raise NotFoundError(f"Task #{task_id} does not exist.")
        if status not in TASK_STATUSES:
            raise ValidationError(f"Task status must be one of {TASK_STATUSES}.")
        is_manager = has_permission(self.db, employee, "task.manage")
        if not is_manager and not has_permission(self.db, employee, "task.update_own"):
            from ..utils.exceptions import AuthorizationError

            raise AuthorizationError(
                "Permission denied: 'task.update_own' is required.")
        if not is_manager and int(task["assigned_to"] or 0) != employee.id:
            from ..utils.exceptions import AuthorizationError

            raise AuthorizationError(
                "Only the assigned staff member or a Manager can update this task.")
        current = task["status"]
        if status not in _STATUS_TRANSITIONS.get(current, ()):
            raise ValidationError(
                f"Cannot move a task from {current!r} to {status!r}.")
        with self.db.transaction() as conn:
            ops_repo.update_task(self.db, task_id, status=status, conn=conn)
            ops_repo.insert_task_history(
                self.db, task_id=task_id, employee_id=employee.id,
                action="status_change", old_value=current, new_value=status,
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="task.status_change",
                entity_type="task", entity_id=task_id,
                old_value={"status": current}, new_value={"status": status},
                description=f"Task #{task_id} status {current} -> {status}."
                            + (f" Note: {note}" if note else ""),
                conn=conn)
            if task.get("assigned_to") and int(task["assigned_to"]) != employee.id:
                notify(self.db, type="task_assigned",
                       recipient_employee_id=int(task["assigned_to"]),
                       title=f"Task update: {task['name']}",
                       body=f"Task '{task['name']}' status changed to {status}.",
                       entity_type="task", entity_id=task_id, conn=conn)
        return self._row_to_task(ops_repo.get_task(self.db, task_id))

    def update_task_details(self, employee, task_id: int, *,
                            name: str | None = None,
                            description: str | None = None,
                            priority: str | None = None,
                            due_at: str | None = None,
                            service_id: int | None = None) -> Task:
        """Edit a generated checklist task (SRS §7.2: the Manager can
        edit the generated checklist for that event). Completed tasks
        are final and cannot be edited. ``service_id`` links the task
        to a catalog service (SRS §7.3, optional)."""
        require_permission(self.db, employee, "task.manage")
        task = ops_repo.get_task(self.db, task_id)
        if task is None:
            raise NotFoundError(f"Task #{task_id} does not exist.")
        if task["status"] == "completed":
            raise ValidationError("A completed task cannot be edited.")
        if name is not None:
            require_text(name, "Task name", max_length=100)
        if priority is not None and priority not in TASK_PRIORITIES:
            raise ValidationError(
                f"Task priority must be one of {TASK_PRIORITIES}.")
        if due_at is not None:
            from ..utils.validators import parse_iso_datetime

            parse_iso_datetime(due_at, "Due date/time")
        if service_id is not None:
            from ..data.repositories import catalog_repository

            if catalog_repository.get_service(self.db, service_id) is None:
                raise NotFoundError(f"Service #{service_id} does not exist.")
        if all(v is None for v in (name, description, priority, due_at,
                                    service_id)):
            raise ValidationError("Nothing to update.")
        old = {k: task[k] for k in ("name", "description", "priority",
                                    "due_at", "service_id")}
        new_values: dict = {}
        if name is not None:
            new_values["name"] = name.strip()
        if description is not None:
            new_values["description"] = description
        if priority is not None:
            new_values["priority"] = priority
        if due_at is not None:
            new_values["due_at"] = due_at
        if service_id is not None:
            new_values["service_id"] = service_id
        changed = list(new_values)
        with self.db.transaction() as conn:
            ops_repo.update_task(
                self.db, task_id, name=new_values.get("name"),
                description=new_values.get("description"),
                priority=new_values.get("priority"),
                due_at=new_values.get("due_at"),
                service_id=new_values.get("service_id"), conn=conn)
            ops_repo.insert_task_history(
                self.db, task_id=task_id, employee_id=employee.id,
                action="edited",
                old_value="; ".join(f"{k}={old[k]}" for k in changed),
                new_value="; ".join(f"{k}={new_values[k]}" for k in changed),
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="task.edit",
                entity_type="task", entity_id=task_id,
                old_value={k: old[k] for k in changed},
                new_value=new_values,
                description=f"Task #{task_id} edited ({', '.join(changed)}).",
                conn=conn)
        return self._row_to_task(ops_repo.get_task(self.db, task_id))

    @staticmethod
    def _row_to_task(row: dict) -> Task:
        return Task(id=int(row["id"]),
                    event_plan_id=(int(row["event_plan_id"])
                                   if row["event_plan_id"] is not None else None),
                    booking_id=(int(row["booking_id"])
                                if row.get("booking_id") is not None else None),
                    service_id=row.get("service_id"),
                    name=row["name"], description=row["description"] or "",
                    assigned_to=row["assigned_to"], due_at=_parse(row["due_at"]),
                    status=row["status"], priority=row["priority"])

    def sweep_task_notifications(self, now: datetime | None = None) -> dict:
        """Lazy task-due warnings (Phase 6, FR-060, GAP G-11 — no
        background process): tasks due within 24h fire ``task_due_soon``
        to the assignee; tasks past due and not completed fire
        ``task_overdue`` (high priority). Each fires at most once per
        task per 24h (A-50 pattern). Returns counts, for tests."""
        now = now or datetime.now()
        since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        due_soon = 0
        overdue = 0
        for row in ops_repo.list_tasks(self.db):
            if (row["status"] == "completed" or not row["due_at"]
                    or row["assigned_to"] is None):
                continue
            due = _parse(row["due_at"])
            if due is None:
                continue
            task_id = int(row["id"])
            if due < now:
                kind, title = "task_overdue", f"Task overdue — {row['name']}"
                body = (f"Task {row['name']!r} was due {row['due_at']} "
                        "and is not completed.")
                if notif_repo.exists_notification(
                        self.db, type=kind, entity_type="task",
                        entity_id=task_id, since_iso=since):
                    continue
                notify(self.db, type=kind, title=title, body=body,
                       recipient_employee_id=int(row["assigned_to"]),
                       entity_type="task", entity_id=task_id)
                overdue += 1
            elif due <= now + timedelta(hours=24):
                kind = "task_due_soon"
                if notif_repo.exists_notification(
                        self.db, type=kind, entity_type="task",
                        entity_id=task_id, since_iso=since):
                    continue
                notify(self.db, type=kind,
                       title=f"Task due soon — {row['name']}",
                       body=f"Task {row['name']!r} is due {row['due_at']}.",
                       recipient_employee_id=int(row["assigned_to"]),
                       entity_type="task", entity_id=task_id)
                due_soon += 1
        return {"task_due_soon": due_soon, "task_overdue": overdue}

    def list_tasks(self, employee, *, event_plan_id: int | None = None,
                   assigned_to: int | None = None,
                   status: str | None = None) -> list[dict]:
        """Managers see everything; staff see only their own tasks
        (assigned_to is forced to their id). Reading tasks also runs
        the lazy task-due sweep (FR-060) — best-effort, like the
        booking hold sweep (A-50)."""
        try:
            self.sweep_task_notifications()
        except Exception:  # pragma: no cover - notifications are best-effort
            logger.warning("Task notification sweep failed.")
        if not has_permission(self.db, employee, "task.manage"):
            assigned_to = employee.id
        return ops_repo.list_tasks(self.db, event_plan_id=event_plan_id,
                                   assigned_to=assigned_to, status=status)

    def task_history(self, employee, task_id: int) -> list[dict]:
        require_permission(self.db, employee, "task.view")
        if ops_repo.get_task(self.db, task_id) is None:
            raise NotFoundError(f"Task #{task_id} does not exist.")
        return ops_repo.list_task_history(self.db, task_id)
