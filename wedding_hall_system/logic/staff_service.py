"""Staff profiles and availability (Phase 4, SRS §7.1).

Employees ARE the login accounts from Phase 1 — there is no parallel
employee concept (GAP G-20). This service manages their staff-profile
fields (name, contact, specialties, active status), their availability
schedule / time off (entered by management), and their assignment
history. No leave-request workflow, attendance, or payroll (SRS §7.1).
"""
from __future__ import annotations

from ..data.database import DatabaseManager
from ..data.repositories import employee_repository, operations_repository as ops_repo
from ..utils.exceptions import NotFoundError, ValidationError
from ..utils.validators import normalize_email, normalize_phone, require_text
from . import audit_service
from .models import Employee, EmployeeAvailability
from .permissions import has_permission, require_permission


def _require_manager_or_admin(db: DatabaseManager, employee: Employee) -> None:
    if not (has_permission(db, employee, "task.manage")
            or has_permission(db, employee, "user.manage")):
        from ..utils.exceptions import AuthorizationError

        raise AuthorizationError(
            "Permission denied: 'task.manage' or 'user.manage' is required.")


class StaffService:
    """Staff profile + availability operations."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    # -- profiles ---------------------------------------------------------
    def list_employees(self, employee: Employee) -> list[dict]:
        _require_manager_or_admin(self.db, employee)
        out: list[dict] = []
        for row in employee_repository.list_employees(self.db):
            roles = employee_repository.get_roles(self.db, int(row["id"]))
            out.append({
                "id": int(row["id"]), "username": row["username"],
                "full_name": row["full_name"], "phone": row["phone"] or "",
                "email": row["email"] or "",
                "specialties": row["specialties"] if "specialties" in row.keys() else "",
                "is_active": bool(row["is_active"]), "roles": roles,
            })
        return out

    def get_profile(self, employee: Employee, target_id: int) -> dict:
        _require_manager_or_admin(self.db, employee)
        row = employee_repository.get_by_id(self.db, target_id)
        if row is None:
            raise NotFoundError(f"Employee #{target_id} does not exist.")
        tasks = ops_repo.list_tasks(self.db, assigned_to=target_id)
        counts = {"pending": 0, "in_progress": 0, "completed": 0}
        for task in tasks:
            if task["status"] in counts:
                counts[task["status"]] += 1
        return {
            "id": int(row["id"]), "username": row["username"],
            "full_name": row["full_name"], "phone": row["phone"] or "",
            "email": row["email"] or "",
            "specialties": row["specialties"] if "specialties" in row.keys() else "",
            "is_active": bool(row["is_active"]),
            "roles": employee_repository.get_roles(self.db, target_id),
            "availability": ops_repo.list_availability(self.db, employee_id=target_id),
            "task_counts": counts,
            "tasks": tasks,
        }

    def update_profile(self, employee: Employee, target_id: int, *,
                       full_name: str | None = None, phone: str | None = None,
                       email: str | None = None,
                       specialties: str | None = None,
                       is_active: bool | None = None) -> dict:
        """Update a staff profile (Owner/Manager with user.manage).
        Only field names are audited, never the values (A-32 pattern).
        Deactivating an account (``is_active=False``) is Owner-only and
        never allowed on your own account."""
        require_permission(self.db, employee, "user.manage")
        row = employee_repository.get_by_id(self.db, target_id)
        if row is None:
            raise NotFoundError(f"Employee #{target_id} does not exist.")
        if is_active is not None and "Owner" not in (employee.roles or []):
            from ..utils.exceptions import AuthorizationError

            raise AuthorizationError(
                "Only the Owner can activate or deactivate an account.")
        if is_active is False and target_id == employee.id:
            raise ValidationError("You cannot deactivate your own account.")
        changed: list[str] = []
        if full_name is not None:
            require_text(full_name, "Full name", max_length=200)
            changed.append("full_name")
        if phone is not None:
            phone = normalize_phone(phone) if phone else ""
            changed.append("phone")
        if email is not None:
            email = normalize_email(email) if email else ""
            changed.append("email")
        if specialties is not None:
            specialties = str(specialties or "")[:500]
            changed.append("specialties")
        if is_active is not None:
            changed.append("is_active")
        if not changed:
            raise ValidationError("Nothing to update.")
        employee_repository.update_employee(
            self.db, target_id, full_name=full_name, phone=phone,
            email=email, specialties=specialties, is_active=is_active)
        audit_service.record(
            self.db, employee_id=employee.id, action="employee.profile_update",
            entity_type="employee", entity_id=target_id,
            new_value={"changed_fields": changed},
            description=f"Profile of employee #{target_id} updated ({', '.join(changed)}).")
        return self.get_profile(employee, target_id)

    def set_roles(self, employee: Employee, target_id: int,
                  roles: list[str]) -> dict:
        """Replace an employee's roles (SRS §7.1: job role is part of
        the staff profile). Owner-only, never on your own account (no
        self-lockout), and never empty — every account keeps at least
        one role. Role changes are audited with the role names."""
        if "Owner" not in (employee.roles or []):
            from ..utils.exceptions import AuthorizationError

            raise AuthorizationError("Only the Owner can change employee roles.")
        if target_id == employee.id:
            raise ValidationError("You cannot change your own roles.")
        row = employee_repository.get_by_id(self.db, target_id)
        if row is None:
            raise NotFoundError(f"Employee #{target_id} does not exist.")
        wanted = [r.strip() for r in (roles or []) if r and r.strip()]
        if not wanted:
            raise ValidationError("An employee needs at least one role.")
        known = employee_repository.list_roles(self.db)
        unknown = [r for r in wanted if r not in known]
        if unknown:
            raise ValidationError(f"Unknown role(s): {', '.join(unknown)}.")
        role_ids = [employee_repository.get_role_id(self.db, r) for r in wanted]
        old_roles = employee_repository.get_roles(self.db, target_id)
        with self.db.transaction() as conn:
            employee_repository.set_roles(
                self.db, target_id, [int(r) for r in role_ids if r is not None],
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="employee.roles_change",
                entity_type="employee", entity_id=target_id,
                old_value={"roles": old_roles}, new_value={"roles": wanted},
                description=f"Roles of employee #{target_id} changed "
                            f"({', '.join(old_roles)} -> {', '.join(wanted)}).",
                conn=conn)
        return self.get_profile(employee, target_id)

    # -- availability -----------------------------------------------------
    def set_availability(self, employee: Employee, target_id: int, *,
                         date: str, start_time: str | None = None,
                         end_time: str | None = None, kind: str = "available",
                         note: str = "") -> dict:
        """Management enters availability / time off (SRS §7.1)."""
        require_permission(self.db, employee, "task.manage")
        if employee_repository.get_by_id(self.db, target_id) is None:
            raise NotFoundError(f"Employee #{target_id} does not exist.")
        entry = EmployeeAvailability(
            employee_id=target_id, date=date, start_time=start_time,
            end_time=end_time, kind=kind, note=note)
        entry_id = ops_repo.insert_availability(
            self.db, employee_id=target_id, date=entry.date,
            start_time=entry.start_time, end_time=entry.end_time,
            kind=entry.kind, note=entry.note)
        audit_service.record(
            self.db, employee_id=employee.id, action="employee.availability_set",
            entity_type="employee_availability", entity_id=entry_id,
            new_value={"employee_id": target_id, "date": date, "kind": kind},
            description=f"Availability for employee #{target_id} on {date}: {kind}.")
        return {"id": entry_id, "employee_id": target_id, "date": entry.date,
                "start_time": entry.start_time, "end_time": entry.end_time,
                "kind": entry.kind, "note": entry.note}

    def list_availability(self, employee: Employee, *, target_id: int | None = None,
                          date: str | None = None) -> list[dict]:
        """Managers see anyone's availability; anyone else sees only
        their own (a staff member checking their own schedule)."""
        if target_id is not None and target_id != employee.id:
            _require_manager_or_admin(self.db, employee)
        if target_id is None and not (
                has_permission(self.db, employee, "task.manage")
                or has_permission(self.db, employee, "user.manage")):
            target_id = employee.id
        return ops_repo.list_availability(self.db, employee_id=target_id, date=date)

    def delete_availability(self, employee: Employee, availability_id: int) -> None:
        require_permission(self.db, employee, "task.manage")
        ops_repo.delete_availability(self.db, availability_id)
        audit_service.record(
            self.db, employee_id=employee.id, action="employee.availability_delete",
            entity_type="employee_availability", entity_id=availability_id,
            description=f"Availability entry #{availability_id} removed.")
