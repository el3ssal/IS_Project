"""General file management (Phase 6, SRS §11).

Attachments link to a customer, booking, contract, payment or expense.
Metadata lives in the ``attachments`` table; bytes live under
``storage/uploads/`` under a random storage name. The directory is
never served statically (GAP G-15): every preview/download/delete goes
through this service, which checks authorization and verifies the
resolved path stays inside the uploads directory.

Validation mirrors the Phase 4 signed-copy upload (contract_service):
extension allowlist (PDF/JPG/PNG — the only preview-safe types, SRS
§11), configurable max size (``attachments.max_size_mb``, seed 10 MB),
magic-byte content check, random 32-hex storage name, sha256 recorded.
The validation is implemented here rather than imported from
contract_service so the tested Phase 4 code stays untouched.

Deletion is a permission-controlled soft-delete (metadata flagged,
stored bytes kept — the Phase 4 pattern) and is audited. Executables
are never stored: the allowlist has no executable extension and the
content check rejects anything that is not really a PDF/JPEG/PNG.
"""
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository, contract_repository as repo
from ..data.repositories import customer_repository, finance_repository
from ..data.repositories import system_repository
from ..utils.exceptions import (
    AttachmentValidationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from ..utils.logging_config import get_logger
from . import audit_service
from .models import (
    ALLOWED_UPLOAD_EXTENSIONS,
    ATTACHMENT_CATEGORIES,
)
from .permissions import has_permission, require_permission

logger = get_logger(__name__)

ATTACHABLE_ENTITIES = ("customer", "booking", "contract", "payment", "expense")

# Entity type -> permission that grants viewing its files. Expenses have
# no dedicated view code: either finance permission that touches
# expenses counts (Accountant holds expense.record, Manager holds
# expense.approve).
_ENTITY_VIEW_PERMISSION: dict[str, str | tuple[str, ...]] = {
    "customer": "customer.view",
    "booking": "booking.view",
    "contract": "contract.view",
    "payment": "payment.view",
    "expense": ("expense.record", "expense.approve"),
}

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

# Preview-safe types (SRS §11): images and PDF only. Every allowed
# upload extension is preview-safe by construction.
PREVIEWABLE_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}


def _can_view_entity(db: DatabaseManager, employee, entity_type: str) -> bool:
    perm = _ENTITY_VIEW_PERMISSION[entity_type]
    if isinstance(perm, tuple):
        return any(has_permission(db, employee, code) for code in perm)
    return has_permission(db, employee, perm)


def _require_entity_view(db: DatabaseManager, employee, entity_type: str) -> None:
    if not _can_view_entity(db, employee, entity_type):
        raise AuthorizationError(
            f"You do not have permission to view {entity_type} files.")


def _entity_exists(db: DatabaseManager, entity_type: str, entity_id: int) -> bool:
    if entity_type == "customer":
        return customer_repository.get_customer(db, entity_id) is not None
    if entity_type == "booking":
        return booking_repository.get_booking(db, entity_id) is not None
    if entity_type == "contract":
        return repo.get_contract(db, entity_id) is not None
    if entity_type == "payment":
        return finance_repository.get_payment(db, entity_id) is not None
    if entity_type == "expense":
        return finance_repository.get_expense(db, entity_id) is not None
    return False


class AttachmentService:
    """Upload / list / preview / download / delete for general files."""

    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self.uploads_dir = self.settings.project_root / "storage" / "uploads"

    # -- upload -------------------------------------------------------------
    def upload(self, employee, *, entity_type: str, entity_id: int,
               src_path: str, original_filename: str,
               category: str = "Other") -> dict:
        """Store a file for an entity. The caller needs
        ``attachment.upload`` plus the view permission of the linked
        entity (a Receptionist may attach to customers/bookings, an
        Accountant to payments/expenses, and so on)."""
        require_permission(self.db, employee, "attachment.upload")
        if entity_type not in ATTACHABLE_ENTITIES:
            raise ValidationError(
                f"Files can attach to {', '.join(ATTACHABLE_ENTITIES)} "
                f"(not {entity_type!r}).")
        _require_entity_view(self.db, employee, entity_type)
        if not _entity_exists(self.db, entity_type, int(entity_id)):
            raise NotFoundError(f"{entity_type.title()} #{entity_id} does not exist.")
        if category not in ATTACHMENT_CATEGORIES:
            raise ValidationError(
                f"Attachment category must be one of {ATTACHMENT_CATEGORIES}.")
        stored_name, mime, size = self._store_upload(src_path, original_filename)
        sha256 = self._sha256(self.uploads_dir / stored_name)
        with self.db.transaction() as conn:
            attachment_id = repo.insert_attachment(
                self.db, entity_type=entity_type, entity_id=int(entity_id),
                original_filename=Path(original_filename).name,
                storage_filename=stored_name, mime_type=mime, size_bytes=size,
                category=category, uploaded_by=employee.id, conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="attachment.upload",
                entity_type="attachment", entity_id=attachment_id,
                new_value={"entity_type": entity_type, "entity_id": int(entity_id),
                           "filename": Path(original_filename).name,
                           "category": category, "sha256": sha256},
                description=f"File {Path(original_filename).name!r} uploaded "
                            f"for {entity_type} #{entity_id}.",
                conn=conn)
        return {"attachment_id": attachment_id, "storage_filename": stored_name,
                "mime_type": mime, "size_bytes": size, "sha256": sha256,
                "category": category}

    def _store_upload(self, src_path: str, original_filename: str) -> tuple[str, str, int]:
        """Validate and store an uploaded file (SRS §11). Returns
        (storage_filename, mime_type, size_bytes). The storage name is
        random — the user-supplied name is metadata only, so path
        traversal through the filename is impossible."""
        from ..config.defaults import DEFAULT_SETTINGS

        if not src_path or not Path(src_path).is_file():
            raise AttachmentValidationError("No file was uploaded.")
        name = Path(original_filename or "").name
        if not name or "." not in name:
            raise AttachmentValidationError(
                "Uploaded file needs a filename with an extension.")
        ext = name.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise AttachmentValidationError(
                f"File type .{ext} is not allowed. Allowed: "
                f"{', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}. "
                "Executable and other types are never stored.")
        try:
            max_mb = int(system_repository.get_setting(
                self.db, "attachments.max_size_mb",
                DEFAULT_SETTINGS.get("attachments.max_size_mb", "10")))
        except ValueError:
            max_mb = 10
        data = Path(src_path).read_bytes()
        if len(data) > max_mb * 1024 * 1024:
            raise AttachmentValidationError(f"File exceeds the {max_mb} MB limit.")
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

    # -- list ---------------------------------------------------------------
    def list_attachments(self, employee, *, entity_type: str | None = None,
                         entity_id: int | None = None,
                         category: str | None = None,
                         uploaded_by: int | None = None,
                         date_from: str | None = None,
                         date_to: str | None = None) -> list[dict]:
        """List attachments, filtered to entities the caller may view
        (SRS §11 search/filter). A caller without the entity's view
        permission never sees its rows — the filter is applied here,
        not in the UI."""
        require_permission(self.db, employee, "attachment.download")
        if entity_type is not None and entity_type not in ATTACHABLE_ENTITIES:
            raise ValidationError(f"Unknown entity type {entity_type!r}.")
        if category is not None and category not in ATTACHMENT_CATEGORIES:
            raise ValidationError(f"Unknown category {category!r}.")
        rows = repo.list_attachments(
            self.db, entity_type=entity_type, entity_id=entity_id,
            category=category)
        visible: list[dict] = []
        for row in rows:
            if not _can_view_entity(self.db, employee, row["entity_type"]):
                continue
            if uploaded_by is not None and int(row["uploaded_by"] or 0) != int(uploaded_by):
                continue
            day = str(row.get("uploaded_at", ""))[:10]
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
            visible.append(row)
        return visible

    # -- download / preview ---------------------------------------------------
    def _resolve(self, attachment_id: int) -> tuple[dict, Path]:
        row = repo.get_attachment(self.db, attachment_id)
        if row is None:
            raise NotFoundError(f"Attachment #{attachment_id} does not exist.")
        path = (self.uploads_dir / row["storage_filename"]).resolve()
        if path.parent != self.uploads_dir.resolve():
            raise ValidationError(
                "Attachment path is outside the uploads directory.")
        if not path.is_file():
            raise NotFoundError("The stored file is missing from disk.")
        return row, path

    def download(self, employee, attachment_id: int) -> Path:
        """Authorized download only (GAP G-15, SRS §11): the caller
        needs ``attachment.download`` plus the linked entity's view
        permission."""
        require_permission(self.db, employee, "attachment.download")
        row, path = self._resolve(attachment_id)
        _require_entity_view(self.db, employee, row["entity_type"])
        return path

    def preview(self, employee, attachment_id: int) -> tuple[Path, str]:
        """Authorized preview (SRS §11): only image/PDF attachments are
        previewable — every allowed upload type is, by construction.
        Returns (path, mime_type); the UI renders images inline and
        offers PDFs as a file."""
        require_permission(self.db, employee, "attachment.download")
        row, path = self._resolve(attachment_id)
        _require_entity_view(self.db, employee, row["entity_type"])
        if row["mime_type"] not in PREVIEWABLE_MIME_TYPES:
            raise ValidationError(
                f"Files of type {row['mime_type']} cannot be previewed.")
        return path, row["mime_type"]

    # -- delete ---------------------------------------------------------------
    def delete(self, employee, attachment_id: int) -> None:
        """Permission-controlled soft-delete (SRS §11): metadata flagged
        deleted, stored bytes kept, audit record written. Only holders
        of ``attachment.delete`` (Manager/Owner) may delete."""
        require_permission(self.db, employee, "attachment.delete")
        row = repo.get_attachment(self.db, attachment_id)
        if row is None:
            raise NotFoundError(f"Attachment #{attachment_id} does not exist.")
        _require_entity_view(self.db, employee, row["entity_type"])
        repo.soft_delete_attachment(self.db, attachment_id)
        audit_service.record(
            self.db, employee_id=employee.id, action="attachment.delete",
            entity_type="attachment", entity_id=attachment_id,
            old_value={"filename": row["original_filename"],
                       "entity_type": row["entity_type"],
                       "entity_id": row["entity_id"]},
            description=f"Attachment {row['original_filename']!r} deleted "
                        "(metadata soft-deleted; stored bytes kept).")
