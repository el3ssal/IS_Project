"""Customer management (Phase 3, business layer — SRS §5).

Duplicate detection (SRS §5.2, decision A-22): an exact match on the
normalized phone or on the National ID is a CONFIRMED duplicate and
always blocks creation; an email-only match is a POSSIBLE duplicate —
the service raises ValidationError carrying the matches and the caller
may retry with ``force=True`` after staff review. Records are never
merged automatically. The full National ID is never exposed in a
duplicate warning, a list, or an audit row (FR-012); lists mask it
unless the caller holds ``customer.view_sensitive``.
"""
from __future__ import annotations

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository as booking_repo
from ..data.repositories import customer_repository as repo
from ..utils.exceptions import NotFoundError, ValidationError
from ..utils.logging_config import get_logger
from ..utils.validators import normalize_email, normalize_phone
from . import audit_service
from .models import Customer, CustomerInteraction, mask_national_id
from .permissions import has_permission, require_permission

logger = get_logger(__name__)


def _row_to_customer(row: dict) -> Customer:
    return Customer(
        id=row["id"], full_name=row["full_name"], phone=row["phone"],
        phone_normalized=row["phone_normalized"], email=row["email"],
        email_normalized=row["email_normalized"], address=row["address"],
        national_id=row["national_id"], notes=row["notes"],
        additional_phones=list(row.get("additional_phones") or []),
    )


class CustomerService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- duplicates ---------------------------------------------------------
    def find_duplicates(self, employee, *, phone: str = "", email: str = "",
                        national_id: str = "") -> list[dict]:
        """Possible/confirmed duplicate matches for review (UC-01 step 4).
        Each match is {"id", "full_name", "match": "phone"|"email"|"national_id"}.
        The National ID value is never included."""
        require_permission(self.db, employee, "customer.view")
        matches: dict[int, dict] = {}
        phone_norm = normalize_phone(phone) if phone else ""
        email_norm = normalize_email(email) if email else ""
        national_id = str(national_id or "").strip()
        for row in repo.find_by_phone(self.db, phone_norm):
            matches[row["id"]] = {"id": row["id"], "full_name": row["full_name"],
                                  "match": "phone"}
        for row in repo.find_by_national_id(self.db, national_id):
            matches[row["id"]] = {"id": row["id"], "full_name": row["full_name"],
                                  "match": "national_id"}
        for row in repo.find_by_email(self.db, email_norm):
            matches.setdefault(row["id"], {"id": row["id"], "full_name": row["full_name"],
                                           "match": "email"})
        return list(matches.values())

    def _mask(self, employee, customer: Customer) -> Customer:
        """Mask the National ID unless the caller may see it (FR-012)."""
        if has_permission(self.db, employee, "customer.view_sensitive"):
            return customer
        customer.national_id = customer.masked_national_id()
        return customer

    # -- CRUD ---------------------------------------------------------------
    def create_customer(self, employee, *, full_name: str, phone: str,
                        email: str = "", address: str = "", national_id: str = "",
                        notes: str = "", additional_phones: list[str] | None = None,
                        force: bool = False) -> Customer:
        """Create a customer (UC-01). Confirmed duplicates (phone or
        National ID) always block; email-only matches block unless the
        caller passes ``force=True`` after staff review (decision A-22)."""
        require_permission(self.db, employee, "customer.create")
        if not str(phone or "").strip():
            raise ValidationError("Primary phone is required.")
        customer = Customer(
            full_name=full_name, phone=phone, email=email, address=address,
            national_id=national_id, notes=notes,
            additional_phones=additional_phones or [],
        )
        matches = self.find_duplicates(
            employee, phone=customer.phone_normalized,
            email=customer.email_normalized, national_id=customer.national_id,
        )
        confirmed = [m for m in matches if m["match"] in ("phone", "national_id")]
        if confirmed:
            names = ", ".join(
                f"{m['full_name']} (same {m['match']})" for m in confirmed)
            err = ValidationError(
                "A customer with the same phone or National ID already exists: "
                f"{names}. Creation blocked — use the existing record.")
            err.duplicates = confirmed  # type: ignore[attr-defined]
            raise err
        possible = [m for m in matches if m["match"] == "email"]
        if possible and not force:
            names = ", ".join(f"{m['full_name']} (same email)" for m in possible)
            err = ValidationError(
                "Possible duplicate customer found: "
                f"{names}. Review the match and confirm to create anyway.")
            err.duplicates = possible  # type: ignore[attr-defined]
            raise err
        with self.db.transaction() as conn:
            customer_id = repo.insert_customer(
                self.db, full_name=customer.full_name, phone=customer.phone,
                phone_normalized=customer.phone_normalized,
                email=customer.email, email_normalized=customer.email_normalized,
                address=customer.address, national_id=customer.national_id,
                notes=customer.notes, conn=conn)
            for extra in customer.additional_phones:
                repo.insert_contact(self.db, customer_id=customer_id,
                                    phone=extra, conn=conn)
            repo.insert_interaction(
                self.db, customer_id=customer_id, employee_id=employee.id,
                type_="note", summary="Customer record created.", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="customer.created",
                entity_type="customer", entity_id=customer_id,
                new_value={"full_name": customer.full_name,
                           "phone": customer.phone_normalized},
                description=f"Customer '{customer.full_name}' created.",
                conn=conn)
        logger.info("Customer #%s '%s' created", customer_id, customer.full_name)
        created = self.get_customer(employee, customer_id)
        assert created is not None
        return created

    def update_customer(self, employee, customer_id: int, **fields) -> Customer:
        """Update the fields given in ``fields`` (only provided fields
        change). Identity edits are audited with old/new values — the
        National ID is recorded only as 'changed', never its value."""
        require_permission(self.db, employee, "customer.create")
        row = repo.get_customer(self.db, customer_id)
        if row is None:
            raise NotFoundError(f"Customer #{customer_id} does not exist.")
        current = _row_to_customer(row)
        updated = Customer(
            id=current.id,
            full_name=fields.get("full_name", current.full_name),
            phone=fields.get("phone", current.phone),
            email=fields.get("email", current.email),
            address=fields.get("address", current.address),
            national_id=fields.get("national_id", current.national_id),
            notes=fields.get("notes", current.notes),
            additional_phones=fields.get("additional_phones", current.additional_phones),
        )
        # Duplicate check on the NEW identity (excluding this customer).
        matches = [m for m in self.find_duplicates(
            employee, phone=updated.phone_normalized,
            email=updated.email_normalized, national_id=updated.national_id,
        ) if m["id"] != customer_id]
        if [m for m in matches if m["match"] in ("phone", "national_id")]:
            raise ValidationError(
                "Another customer already uses this phone or National ID.")
        old_value = {"full_name": current.full_name, "phone": current.phone_normalized,
                     "email": current.email_normalized}
        new_value = {"full_name": updated.full_name, "phone": updated.phone_normalized,
                     "email": updated.email_normalized}
        if updated.national_id != current.national_id:
            old_value["national_id"] = "changed"
            new_value["national_id"] = "changed"
        with self.db.transaction() as conn:
            repo.update_customer(
                self.db, customer_id, full_name=updated.full_name,
                phone=updated.phone, phone_normalized=updated.phone_normalized,
                email=updated.email, email_normalized=updated.email_normalized,
                address=updated.address, national_id=updated.national_id,
                notes=updated.notes, conn=conn)
            if "additional_phones" in fields:
                repo.delete_contacts(self.db, customer_id, conn=conn)
                for extra in updated.additional_phones:
                    repo.insert_contact(self.db, customer_id=customer_id,
                                        phone=extra, conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="customer.updated",
                entity_type="customer", entity_id=customer_id,
                old_value=old_value, new_value=new_value,
                description=f"Customer '{updated.full_name}' updated.", conn=conn)
        return self.get_customer(employee, customer_id)

    def get_customer(self, employee, customer_id: int) -> Customer | None:
        require_permission(self.db, employee, "customer.view")
        row = repo.get_customer(self.db, customer_id)
        if row is None:
            return None
        return self._mask(employee, _row_to_customer(row))

    def list_customers(self, employee, *, search: str = "") -> list[Customer]:
        require_permission(self.db, employee, "customer.view")
        return [self._mask(employee, _row_to_customer(row))
                for row in repo.list_customers(self.db, search=search)]

    # -- interactions / profile ----------------------------------------------
    def add_interaction(self, employee, customer_id: int, type_: str,
                        summary: str) -> CustomerInteraction:
        require_permission(self.db, employee, "customer.create")
        if repo.get_customer(self.db, customer_id) is None:
            raise NotFoundError(f"Customer #{customer_id} does not exist.")
        interaction = CustomerInteraction(
            customer_id=customer_id, employee_id=employee.id,
            type=type_, summary=summary)
        interaction.id = repo.insert_interaction(
            self.db, customer_id=customer_id, employee_id=employee.id,
            type_=interaction.type, summary=interaction.summary)
        return interaction

    def list_interactions(self, employee, customer_id: int) -> list[CustomerInteraction]:
        require_permission(self.db, employee, "customer.view")
        if repo.get_customer(self.db, customer_id) is None:
            raise NotFoundError(f"Customer #{customer_id} does not exist.")
        return [CustomerInteraction(
            id=r["id"], customer_id=r["customer_id"], employee_id=r["employee_id"],
            type=r["type"], summary=r["summary"])
            for r in repo.list_interactions(self.db, customer_id)]

    def profile(self, employee, customer_id: int) -> dict:
        """Customer profile (SRS §5.1): masked record, bookings,
        quotations, interactions and the three distinct money totals
        (contracted / collected / outstanding — never conflated)."""
        require_permission(self.db, employee, "customer.view")
        customer = self.get_customer(employee, customer_id)
        if customer is None:
            raise NotFoundError(f"Customer #{customer_id} does not exist.")
        totals = booking_repo.customer_totals(self.db, customer_id)
        return {
            "customer": customer,
            "totals": totals,
            "bookings": booking_repo.list_bookings(self.db, customer_id=customer_id),
            "quotations": booking_repo.list_quotations(self.db, customer_id=customer_id),
            "interactions": self.list_interactions(employee, customer_id),
        }
