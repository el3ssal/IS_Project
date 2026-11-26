"""Contracts (Phase 4, SRS §6).

Templates are rows in ``system_settings`` (no ``contract_templates``
table — ARCHITECTURE_REVIEW §3.5): key ``contract.template.<slug>``
holding JSON ``{"name": ..., "body": ...}``. Contracts are generated
from Confirmed bookings as VERSIONED HTML documents (A-38: no PDF
library is permitted yet, so the printable HTML document is the
contract; a browser print-to-PDF produces the PDF). The rendered HTML
is frozen in ``contract_versions.body`` and also written to
``storage/contracts/<number>_v<n>.html``. Editing a signed contract
creates a new version and preserves the old one; the new version
returns to ``pending_signature``.

The contract body contains the customer's full National ID by design
(a contract is a legal document); access is restricted to holders of
``contract.view`` / ``contract.manage``, and the ID is never written
into audit JSON (A-32 pattern).

Signed copies are uploaded manually (SRS §6): files are validated
(extension allowlist, size, magic bytes), stored under
``storage/uploads/`` with a random name, and served only through the
authorized ``download_attachment`` handler (GAP G-15).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime
from pathlib import Path

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository, contract_repository as repo
from ..data.repositories import customer_repository, system_repository
from ..data.repositories import catalog_repository
from ..utils.exceptions import (
    AttachmentValidationError,
    NotFoundError,
    ValidationError,
)
from ..utils.logging_config import get_logger
from ..utils.validators import validate_date_yyyy_mm_dd
from . import audit_service
from .models import (
    ALLOWED_UPLOAD_EXTENSIONS,
    ATTACHMENT_CATEGORIES,
    CONTRACT_PLACEHOLDERS,
    CONTRACT_SIGNATURE_STATUSES,
    Contract,
    ContractVersion,
)
from .notification_service import notify
from .permissions import require_permission

logger = get_logger(__name__)

TEMPLATE_PREFIX = "contract.template."
GENERATABLE_STATUSES = ("confirmed", "in_progress", "completed")
_SIGNATURE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("pending_signature", "cancelled"),
    "pending_signature": ("partially_signed", "signed", "cancelled"),
    "partially_signed": ("signed", "cancelled"),
    "signed": ("cancelled",),
    "cancelled": (),
}

# Magic bytes for the SRS §11 allowlist (validated by content, not
# just extension). PDF starts with %PDF; JPEG FF D8 FF; PNG 89 50 4E 47.
_MAGIC_BYTES: dict[str, list[bytes]] = {
    "pdf": [b"%PDF"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG"],
}

_MIME_TYPES = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _validate_template_body(body: str) -> None:
    if not str(body or "").strip():
        raise ValidationError("Contract template body must not be empty.")
    unknown = sorted(set(_PLACEHOLDER_RE.findall(body)) - set(CONTRACT_PLACEHOLDERS))
    if unknown:
        raise ValidationError(
            f"Unknown placeholders in contract template: {', '.join(unknown)}. "
            f"Allowed: {', '.join(CONTRACT_PLACEHOLDERS)}.")


def _render_template(body: str, context: dict) -> str:
    def replace(match: re.Match) -> str:
        return str(context.get(match.group(1), ""))

    return _PLACEHOLDER_RE.sub(replace, body)


class ContractService:
    """Contract templates, generation, versioning, signatures, uploads."""

    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self.contracts_dir = self.settings.project_root / "storage" / "contracts"
        self.uploads_dir = self.settings.project_root / "storage" / "uploads"

    # -- templates (settings rows) ----------------------------------------
    def list_templates(self, employee) -> list[dict]:
        require_permission(self.db, employee, "contract.view")
        rows = system_repository.list_settings_by_prefix(self.db, TEMPLATE_PREFIX)
        templates: list[dict] = []
        for row in rows:
            try:
                data = json.loads(row["value"])
                templates.append({"name": data.get("name", ""),
                                  "key": row["key"], "body": data.get("body", "")})
            except ValueError:
                logger.warning("Ignoring unparsable contract template %s.", row["key"])
        return templates

    def get_template(self, employee, name: str) -> dict:
        require_permission(self.db, employee, "contract.view")
        raw = system_repository.get_setting(
            self.db, TEMPLATE_PREFIX + _slug(name), "")
        if not raw:
            raise NotFoundError(f"Contract template {name!r} does not exist.")
        return json.loads(raw)

    def create_template(self, employee, *, name: str, body: str) -> dict:
        require_permission(self.db, employee, "contract.manage")
        if not str(name or "").strip():
            raise ValidationError("Template name must not be empty.")
        _validate_template_body(body)
        key = TEMPLATE_PREFIX + _slug(name)
        if system_repository.get_setting(self.db, key, ""):
            raise ValidationError(f"A contract template named {name!r} already exists.")
        data = {"name": name.strip(), "body": body}
        system_repository.set_setting(self.db, key, json.dumps(data, ensure_ascii=False))
        audit_service.record(
            self.db, employee_id=employee.id, action="contract.template_create",
            entity_type="contract_template", entity_id=None,
            new_value={"name": name.strip()},
            description=f"Contract template {name!r} created.")
        return data

    def update_template(self, employee, *, name: str, body: str) -> dict:
        require_permission(self.db, employee, "contract.manage")
        _validate_template_body(body)
        key = TEMPLATE_PREFIX + _slug(name)
        if not system_repository.get_setting(self.db, key, ""):
            raise NotFoundError(f"Contract template {name!r} does not exist.")
        data = {"name": name.strip(), "body": body}
        system_repository.set_setting(self.db, key, json.dumps(data, ensure_ascii=False))
        audit_service.record(
            self.db, employee_id=employee.id, action="contract.template_update",
            entity_type="contract_template", entity_id=None,
            new_value={"name": name.strip()},
            description=f"Contract template {name!r} updated (existing "
                        "contracts are unaffected — versions are frozen).")
        return data

    def delete_template(self, employee, *, name: str) -> None:
        require_permission(self.db, employee, "contract.manage")
        key = TEMPLATE_PREFIX + _slug(name)
        if not system_repository.get_setting(self.db, key, ""):
            raise NotFoundError(f"Contract template {name!r} does not exist.")
        in_use = repo.count_contracts_by_template(self.db, name.strip())
        if in_use:
            raise ValidationError(
                f"Template {name!r} is used by {in_use} contract(s) and "
                "cannot be deleted.")
        system_repository.delete_setting(self.db, key)
        audit_service.record(
            self.db, employee_id=employee.id, action="contract.template_delete",
            entity_type="contract_template", entity_id=None,
            new_value={"name": name.strip()},
            description=f"Contract template {name!r} deleted.")

    # -- generation / versioning ------------------------------------------
    def _next_number(self, conn) -> str:
        today = datetime.now().strftime("%Y%m%d")
        day_prefix = f"CT-{today}-"
        seq = repo.count_contracts_on_day(self.db, day_prefix, conn=conn) + 1
        return f"{day_prefix}{seq:04d}"

    def _context(self, booking: dict) -> dict:
        """Render context for a booking. Amounts are formatted EGP
        strings; the customer's full National ID is included by design
        (legal document — see the module docstring)."""
        booking = dict(booking)  # sqlite3.Row has no .get()
        customer = customer_repository.get_customer(self.db, int(booking["customer_id"]))
        hall = catalog_repository.get_hall(self.db, int(booking["hall_id"]))
        package = None
        if booking.get("package_id"):
            package = catalog_repository.get_package(self.db, int(booking["package_id"]))
        event_type = None
        if booking.get("event_type_id"):
            row = self.db.query_one("SELECT name FROM event_types WHERE id = ?",
                                    (int(booking["event_type_id"]),))
            event_type = row["name"] if row else ""
        start = _parse(booking["start_at"])
        end = _parse(booking["end_at"])
        total = int(booking["total_amount"])
        deposit_paid = int(booking["deposit_paid"])
        policy_hold = system_repository.get_setting(self.db, "booking.hold_hours", "48")
        policy_grace = system_repository.get_setting(self.db, "booking.grace_hours", "24")
        policy_deposit = system_repository.get_setting(
            self.db, "booking.deposit_percent", "30")
        return {
            "contract_number": "",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "venue_name": system_repository.get_setting(
                self.db, "venue.name", self.settings.venue_name),
            "customer_name": customer["full_name"] if customer else "",
            "customer_phone": customer["phone"] if customer else "",
            "customer_address": customer["address"] if customer else "",
            "customer_national_id": customer["national_id"] if customer else "",
            "hall_name": hall["name"] if hall else "",
            "event_type": event_type or "",
            "package_name": package["name"] if package else "",
            "event_date": start.strftime("%Y-%m-%d") if start else "",
            "event_start": start.strftime("%Y-%m-%d %H:%M") if start else "",
            "event_end": end.strftime("%Y-%m-%d %H:%M") if end else "",
            "guest_count": str(booking["guest_count"]),
            "total_amount": format_money(total, self.settings.currency),
            "deposit_required": format_money(int(booking["deposit_required"]),
                                             self.settings.currency),
            "deposit_paid": format_money(deposit_paid, self.settings.currency),
            "outstanding": format_money(max(0, total - deposit_paid),
                                        self.settings.currency),
            "hold_hours": policy_hold,
            "grace_hours": policy_grace,
            "deposit_percent": policy_deposit,
            "booking_number": booking["number"],
        }

    def _write_contract_file(self, number: str, version: int, body: str) -> Path:
        self.contracts_dir.mkdir(parents=True, exist_ok=True)
        path = self.contracts_dir / f"{number}_v{version}.html"
        path.write_text(body, encoding="utf-8")
        return path

    def generate_contract(self, employee, booking_id: int,
                          template_name: str) -> Contract:
        """Generate the first contract version for a Confirmed booking.
        One contract per booking — later edits create new versions."""
        require_permission(self.db, employee, "contract.manage")
        booking = booking_repository.get_booking(self.db, booking_id)
        if booking is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        if booking["status"] not in GENERATABLE_STATUSES:
            raise ValidationError(
                f"Contracts can only be generated for Confirmed bookings "
                f"(current status: {booking['status']}).")
        if repo.get_contract_by_booking(self.db, booking_id) is not None:
            raise ValidationError(
                "This booking already has a contract — create a new version "
                "instead of generating a second contract.")
        template = self.get_template(employee, template_name)
        context = self._context(booking)
        with self.db.transaction() as conn:
            number = self._next_number(conn)
            context["contract_number"] = number
            body = _render_template(template["body"], context)
            contract_id = repo.insert_contract(
                self.db, number=number, booking_id=booking_id,
                template_name=template["name"], conn=conn)
            version_id = repo.insert_version(
                self.db, contract_id=contract_id, version=1, body=body,
                created_by=employee.id, conn=conn)
            repo.set_current_version(self.db, contract_id, version_id, conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="contract.generate",
                entity_type="contract", entity_id=contract_id,
                new_value={"number": number, "template": template["name"]},
                description=f"Contract {number} generated for booking "
                            f"{booking['number']} (version 1).",
                conn=conn)
            notify(self.db, type="contract_awaiting_signature",
                   title=f"Contract {number} awaits signature",
                   body=f"Contract {number} for booking {booking['number']} "
                        "was generated and awaits signature.",
                   entity_type="contract", entity_id=contract_id, conn=conn)
        self._write_contract_file(number, 1, body)
        row = repo.get_contract(self.db, contract_id)
        return self._row_to_contract(row)

    def create_new_version(self, employee, contract_id: int, *,
                           template_name: str | None = None,
                           body: str | None = None) -> ContractVersion:
        """Create a new frozen version. Without ``body`` the version is
        re-rendered from the template with the booking's current data;
        with ``body`` the caller supplies the edited document (editing a
        signed contract). The old version is preserved untouched, and a
        signed contract returns to ``pending_signature`` — the new
        version is not signed (SRS §6)."""
        require_permission(self.db, employee, "contract.manage")
        contract = repo.get_contract(self.db, contract_id)
        if contract is None:
            raise NotFoundError(f"Contract #{contract_id} does not exist.")
        if contract["signature_status"] == "cancelled":
            raise ValidationError("A cancelled contract cannot get a new version.")
        if body is None:
            booking = booking_repository.get_booking(
                self.db, int(contract["booking_id"]))
            if booking is None:
                raise NotFoundError("The contract's booking no longer exists.")
            template = self.get_template(
                employee, template_name or contract["template_name"])
            context = self._context(booking)
            context["contract_number"] = contract["number"]
            body = _render_template(template["body"], context)
            used_template = template["name"]
        else:
            if not str(body or "").strip():
                raise ValidationError("Contract version body must not be empty.")
            used_template = contract["template_name"]
        with self.db.transaction() as conn:
            version = repo.max_version(self.db, contract_id, conn=conn) + 1
            version_id = repo.insert_version(
                self.db, contract_id=contract_id, version=version, body=body,
                created_by=employee.id, conn=conn)
            repo.set_current_version(self.db, contract_id, version_id, conn=conn)
            if contract["signature_status"] == "signed":
                repo.update_signature_status(
                    self.db, contract_id, "pending_signature",
                    signature_date="", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="contract.new_version",
                entity_type="contract", entity_id=contract_id,
                new_value={"version": version, "template": used_template},
                description=f"Contract {contract['number']} version {version} "
                            "created; previous versions preserved.",
                conn=conn)
        self._write_contract_file(contract["number"], version, body)
        row = repo.get_version(self.db, contract_id, version)
        return ContractVersion(id=int(row["id"]), contract_id=contract_id,
                               version=version, body=body, created_by=employee.id)

    # -- reads --------------------------------------------------------------
    @staticmethod
    def _row_to_contract(row: dict | None) -> Contract | None:
        if row is None:
            return None
        return Contract(
            id=int(row["id"]), number=row["number"],
            booking_id=int(row["booking_id"]), template_name=row["template_name"],
            current_version_id=(int(row["current_version_id"])
                                if row["current_version_id"] is not None else None),
            signature_status=row["signature_status"],
            signature_date=row["signature_date"] or "",
        )

    def get_contract(self, employee, contract_id: int) -> Contract | None:
        require_permission(self.db, employee, "contract.view")
        return self._row_to_contract(repo.get_contract(self.db, contract_id))

    def list_contracts(self, employee, *, booking_id: int | None = None,
                       signature_status: str | None = None) -> list[Contract]:
        require_permission(self.db, employee, "contract.view")
        return [self._row_to_contract(r) for r in
                repo.list_contracts(self.db, booking_id=booking_id,
                                    signature_status=signature_status)]

    def get_version_body(self, employee, contract_id: int, version: int) -> str:
        require_permission(self.db, employee, "contract.view")
        row = repo.get_version(self.db, contract_id, version)
        if row is None:
            raise NotFoundError(
                f"Contract #{contract_id} has no version {version}.")
        return row["body"]

    def list_versions(self, employee, contract_id: int) -> list[ContractVersion]:
        require_permission(self.db, employee, "contract.view")
        if repo.get_contract(self.db, contract_id) is None:
            raise NotFoundError(f"Contract #{contract_id} does not exist.")
        return [ContractVersion(id=int(r["id"]), contract_id=int(r["contract_id"]),
                                version=int(r["version"]), body=r["body"],
                                created_by=r["created_by"])
                for r in repo.list_versions(self.db, contract_id)]

    # -- signatures ---------------------------------------------------------
    def set_signature_status(self, employee, contract_id: int, status: str,
                             signature_date: str | None = None) -> Contract:
        require_permission(self.db, employee, "contract.manage")
        if status not in CONTRACT_SIGNATURE_STATUSES:
            raise ValidationError(
                f"Signature status must be one of {CONTRACT_SIGNATURE_STATUSES}.")
        contract = repo.get_contract(self.db, contract_id)
        if contract is None:
            raise NotFoundError(f"Contract #{contract_id} does not exist.")
        current = contract["signature_status"]
        if status not in _SIGNATURE_TRANSITIONS.get(current, ()):
            raise ValidationError(
                f"Cannot move a contract from {current!r} to {status!r}.")
        if status == "signed":
            if not signature_date:
                raise ValidationError(
                    "A signature date is required to mark a contract signed.")
            validate_date_yyyy_mm_dd(signature_date, "Signature date")
        with self.db.transaction() as conn:
            repo.update_signature_status(
                self.db, contract_id, status,
                signature_date=signature_date or contract["signature_date"] or "",
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="contract.signature_status",
                entity_type="contract", entity_id=contract_id,
                old_value={"status": current}, new_value={"status": status},
                description=f"Contract {contract['number']} signature status "
                            f"{current} -> {status}.",
                conn=conn)
        return self._row_to_contract(repo.get_contract(self.db, contract_id))

    # -- signed-copy uploads / downloads ------------------------------------
    def upload_signed_copy(self, employee, contract_id: int, src_path: str,
                           original_filename: str, signature_date: str) -> dict:
        """Store a signed contract copy (SRS §6): validate, store under a
        random name, record metadata + sha256, mark the contract signed
        with the recorded signature date."""
        require_permission(self.db, employee, "contract.manage")
        contract = repo.get_contract(self.db, contract_id)
        if contract is None:
            raise NotFoundError(f"Contract #{contract_id} does not exist.")
        if contract["signature_status"] == "cancelled":
            raise ValidationError("A cancelled contract cannot accept a signed copy.")
        validate_date_yyyy_mm_dd(signature_date, "Signature date")
        stored_name, mime, size = self._store_upload(src_path, original_filename)
        sha256 = self._sha256(self.uploads_dir / stored_name)
        with self.db.transaction() as conn:
            attachment_id = repo.insert_attachment(
                self.db, entity_type="contract", entity_id=contract_id,
                original_filename=original_filename, storage_filename=stored_name,
                mime_type=mime, size_bytes=size, category="Contracts",
                uploaded_by=employee.id, conn=conn)
            repo.update_signature_status(
                self.db, contract_id, "signed", signature_date=signature_date,
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="contract.signed_copy_upload",
                entity_type="contract", entity_id=contract_id,
                new_value={"attachment_id": attachment_id,
                           "signature_date": signature_date, "sha256": sha256},
                description=f"Signed copy uploaded for contract "
                            f"{contract['number']} (signature date {signature_date}).",
                conn=conn)
        return {"attachment_id": attachment_id, "storage_filename": stored_name,
                "mime_type": mime, "size_bytes": size, "sha256": sha256}

    def _store_upload(self, src_path: str, original_filename: str) -> tuple[str, str, int]:
        """Validate and store an uploaded file. Returns
        (storage_filename, mime_type, size_bytes). Raises
        AttachmentValidationError on any problem (SRS §11)."""
        from ..config.defaults import DEFAULT_SETTINGS

        if not src_path or not Path(src_path).is_file():
            raise AttachmentValidationError("No file was uploaded.")
        name = Path(original_filename or "").name
        if not name or "." not in name:
            raise AttachmentValidationError("Uploaded file needs a filename with an extension.")
        ext = name.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise AttachmentValidationError(
                f"File type .{ext} is not allowed. Allowed: "
                f"{', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}.")
        max_mb = 10
        try:
            max_mb = int(system_repository.get_setting(
                self.db, "attachments.max_size_mb",
                DEFAULT_SETTINGS.get("attachments.max_size_mb", "10")))
        except ValueError:
            max_mb = 10
        data = Path(src_path).read_bytes()
        if len(data) > max_mb * 1024 * 1024:
            raise AttachmentValidationError(
                f"File exceeds the {max_mb} MB limit.")
        for magic in _MAGIC_BYTES.get(ext, []):
            if not data.startswith(magic):
                raise AttachmentValidationError(
                    f"File content does not match its .{ext} extension.")
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{secrets.token_hex(16)}.{ext}"
        (self.uploads_dir / stored_name).write_bytes(data)
        return stored_name, _MIME_TYPES[ext], len(data)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def download_attachment(self, employee, attachment_id: int) -> Path:
        """Authorized download only (GAP G-15): the caller must hold
        ``attachment.download``; the returned path is verified to sit
        inside the uploads directory."""
        require_permission(self.db, employee, "attachment.download")
        row = repo.get_attachment(self.db, attachment_id)
        if row is None:
            raise NotFoundError(f"Attachment #{attachment_id} does not exist.")
        path = (self.uploads_dir / row["storage_filename"]).resolve()
        if path.parent != self.uploads_dir.resolve():
            raise ValidationError("Attachment path is outside the uploads directory.")
        if not path.is_file():
            raise NotFoundError("The stored file is missing from disk.")
        return path

    def list_attachments(self, employee, contract_id: int) -> list[dict]:
        require_permission(self.db, employee, "contract.view")
        if repo.get_contract(self.db, contract_id) is None:
            raise NotFoundError(f"Contract #{contract_id} does not exist.")
        return repo.list_attachments(self.db, entity_type="contract",
                                     entity_id=contract_id)

    def delete_attachment(self, employee, attachment_id: int) -> None:
        require_permission(self.db, employee, "contract.manage")
        row = repo.get_attachment(self.db, attachment_id)
        if row is None:
            raise NotFoundError(f"Attachment #{attachment_id} does not exist.")
        repo.soft_delete_attachment(self.db, attachment_id)
        audit_service.record(
            self.db, employee_id=employee.id, action="attachment.delete",
            entity_type="attachment", entity_id=attachment_id,
            old_value={"filename": row["original_filename"]},
            description=f"Attachment {row['original_filename']!r} deleted "
                        "(metadata soft-deleted; stored bytes kept).")
