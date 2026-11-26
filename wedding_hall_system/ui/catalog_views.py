"""Halls & pricing screens (Phase 2, presentation tier).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions; a denied
action raises gr.Error with the service's message.

Money is entered in EGP in the UI and converted to integer piasters
before it reaches a service. All seeded rows are EXAMPLE data — the
screens label them as such.
"""
from __future__ import annotations

import gradio as gr

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.availability_service import AvailabilityService
from ..logic.catalog_service import CatalogService
from ..logic.hall_service import HallService
from ..logic.models import ServiceSelection
from ..logic.pricing_service import PricingService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from ..utils.validators import egp_to_piasters, parse_iso_datetime

logger = get_logger(__name__)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def build_catalog_ui(db: DatabaseManager, auth: AuthService,
                     settings: AppSettings, session_token) -> None:
    """Build the Halls & Pricing tabs. Must be called inside a gr.Blocks
    context, after the login/main views exist. ``session_token`` is the
    gr.State holding the login token."""
    halls = HallService(db, settings)
    catalog = CatalogService(db, settings)
    pricing = PricingService(db, settings)
    availability = AvailabilityService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    def _hall_choices():
        return [(f"{h.name} (cap {h.capacity})", h.id)
                for h in halls.list_halls(_system_employee(), include_inactive=True)]

    def _system_employee():
        # Read-only list helpers need an employee with view rights; the
        # Owner demo account always exists (seeded). Used only to build
        # dropdown choices — every write still checks the real user.
        row = db.query_one("SELECT id FROM employees WHERE username = 'owner'")
        from ..logic.models import Employee
        return Employee(id=int(row["id"]), username="owner", full_name="Owner")

    # ============================================================== HALLS
    with gr.Tab("Halls"):
        gr.Markdown("### Halls — seeded rows marked **(example)** are demo data. "
                    "Edit or deactivate them and add the real halls.")
        halls_list = gr.Markdown("")
        refresh_halls_btn = gr.Button("Refresh halls")
        with gr.Row():
            hall_id = gr.Number(label="Hall ID (empty = create new)", precision=0)
            hall_name = gr.Textbox(label="Name")
            hall_capacity = gr.Number(label="Capacity (guests)", precision=0)
            hall_price = gr.Number(label="Base price (EGP per event)")
        with gr.Row():
            hall_status = gr.Dropdown(["active", "inactive", "maintenance"],
                                      value="active", label="Status")
            hall_facilities = gr.Textbox(label="Facilities (comma separated)",
                                         placeholder="AC, Stage, Sound system")
            hall_notes = gr.Textbox(label="Notes")
        hall_desc = gr.Textbox(label="Description")
        with gr.Row():
            hall_save_btn = gr.Button("Save hall", variant="primary")
            hall_deactivate_btn = gr.Button("Deactivate hall")
            hall_reactivate_btn = gr.Button("Reactivate hall")

        def _halls_md():
            rows = []
            for h in halls.list_halls(_system_employee(), include_inactive=True):
                rows.append([h.id, h.name, h.capacity, format_money(h.base_price),
                             h.status, "yes" if h.is_active else "no",
                             ", ".join(h.facilities)])
            return _md_table(["ID", "Name", "Capacity", "Base price", "Status",
                              "Active", "Facilities"], rows)

        def do_refresh_halls(token):
            employee(token)
            return _halls_md()

        def do_save_hall(token, hid, name, capacity, price, status,
                         facilities, notes, desc):
            emp = employee(token)
            try:
                base = egp_to_piasters(price, "Base price (EGP)")
                facs = [f.strip() for f in (facilities or "").split(",") if f.strip()]
                if hid:
                    hall = halls.update_hall(
                        emp, int(hid), name=name, description=desc or "",
                        capacity=int(capacity), base_price=base, status=status,
                        facilities=facs, notes=notes or "")
                    gr.Info(f"Hall '{hall.name}' updated.")
                else:
                    hall = halls.create_hall(
                        emp, name=name, description=desc or "",
                        capacity=int(capacity), base_price=base, status=status,
                        facilities=facs, notes=notes or "")
                    gr.Info(f"Hall '{hall.name}' created.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _halls_md()

        def do_set_hall_active(token, hid, active: bool):
            emp = employee(token)
            if not hid:
                raise gr.Error("Enter a hall ID first.")
            try:
                hall = halls.set_hall_active(emp, int(hid), active)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Hall '{hall.name}' is now {'active' if active else 'inactive'}.")
            return _halls_md()

        refresh_halls_btn.click(do_refresh_halls, inputs=[session_token], outputs=[halls_list])
        hall_save_btn.click(do_save_hall, inputs=[session_token, hall_id, hall_name,
                            hall_capacity, hall_price, hall_status, hall_facilities,
                            hall_notes, hall_desc], outputs=[halls_list])
        hall_deactivate_btn.click(lambda t, i: do_set_hall_active(t, i, False),
                                  inputs=[session_token, hall_id], outputs=[halls_list])
        hall_reactivate_btn.click(lambda t, i: do_set_hall_active(t, i, True),
                                  inputs=[session_token, hall_id], outputs=[halls_list])

    # ============================================================== HOURS
    with gr.Tab("Hours & Closures"):
        gr.Markdown("### Opening hours, exceptional dates and closures")
        hours_list = gr.Markdown("")
        refresh_hours_btn = gr.Button("Refresh hours")
        with gr.Row():
            hours_hall = gr.Dropdown(choices=_hall_choices(), label="Hall")
            hours_weekday = gr.Dropdown(choices=list(enumerate(WEEKDAYS)), label="Weekday")
            hours_open = gr.Textbox(label="Opens (HH:MM)", value="12:00")
            hours_close = gr.Textbox(label="Closes (HH:MM)", value="23:00")
            hours_closed = gr.Checkbox(label="Closed that day", value=False)
        hours_set_btn = gr.Button("Save weekly hours", variant="primary")
        gr.Markdown("#### Exceptional dates (holidays / special openings)")
        with gr.Row():
            sp_hall = gr.Dropdown(choices=_hall_choices(), label="Hall")
            sp_date = gr.Textbox(label="Date (YYYY-MM-DD)", placeholder="2026-12-25")
            sp_open = gr.Textbox(label="Opens (HH:MM, blank if closed)", value="12:00")
            sp_close = gr.Textbox(label="Closes (HH:MM, blank if closed)", value="23:00")
            sp_closed = gr.Checkbox(label="Closed", value=False)
            sp_reason = gr.Textbox(label="Reason")
        with gr.Row():
            sp_add_btn = gr.Button("Add exceptional date", variant="primary")
            sp_del_id = gr.Number(label="Exceptional date ID to delete", precision=0)
            sp_del_btn = gr.Button("Delete exceptional date")

        def _hours_md():
            lines = []
            for h in halls.list_halls(_system_employee(), include_inactive=True):
                lines.append(f"**{h.name}** (ID {h.id})")
                weekly = halls.list_weekly_hours(_system_employee(), h.id)
                wrows = [[WEEKDAYS[w.weekday],
                          "closed" if w.is_closed else f"{w.open_time}–{w.close_time}"]
                         for w in weekly]
                lines.append(_md_table(["Weekday", "Hours"], wrows))
                special = halls.list_special_hours(_system_employee(), h.id)
                srows = [[s.id, s.date,
                          "closed" if s.is_closed else f"{s.open_time}–{s.close_time}",
                          s.reason] for s in special]
                lines.append(_md_table(["ID", "Date", "Hours", "Reason"], srows))
            return "\n\n".join(lines)

        def do_refresh_hours(token):
            employee(token)
            return _hours_md()

        def do_set_hours(token, hid, weekday, open_t, close_t, closed):
            emp = employee(token)
            try:
                halls.set_weekly_hours(emp, int(hid), weekday=int(weekday),
                                       open_time=open_t or "00:00",
                                       close_time=close_t or "00:00",
                                       is_closed=bool(closed))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Weekly hours saved.")
            return _hours_md()

        def do_add_special(token, hid, date, open_t, close_t, closed, reason):
            emp = employee(token)
            try:
                halls.add_special_hours(
                    emp, int(hid), date=date,
                    open_time=None if closed else (open_t or None),
                    close_time=None if closed else (close_t or None),
                    is_closed=bool(closed), reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Exceptional date added.")
            return _hours_md()

        def do_del_special(token, sid):
            emp = employee(token)
            if not sid:
                raise gr.Error("Enter an exceptional date ID first.")
            try:
                halls.delete_special_hours(emp, int(sid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Exceptional date deleted.")
            return _hours_md()

        refresh_hours_btn.click(do_refresh_hours, inputs=[session_token], outputs=[hours_list])
        hours_set_btn.click(do_set_hours, inputs=[session_token, hours_hall,
                            hours_weekday, hours_open, hours_close, hours_closed],
                            outputs=[hours_list])
        sp_add_btn.click(do_add_special, inputs=[session_token, sp_hall, sp_date,
                         sp_open, sp_close, sp_closed, sp_reason], outputs=[hours_list])
        sp_del_btn.click(do_del_special, inputs=[session_token, sp_del_id], outputs=[hours_list])

    # ========================================================== MAINTENANCE
    with gr.Tab("Maintenance"):
        gr.Markdown("### Maintenance blocks — a hall cannot be booked during maintenance")
        maint_list = gr.Markdown("")
        refresh_maint_btn = gr.Button("Refresh maintenance")
        with gr.Row():
            maint_hall = gr.Dropdown(choices=_hall_choices(), label="Hall")
            maint_type = gr.Textbox(label="Type", placeholder="AC repair")
            maint_start = gr.Textbox(label="Start (YYYY-MM-DDTHH:MM)",
                                     placeholder="2026-12-01T09:00")
            maint_end = gr.Textbox(label="End (YYYY-MM-DDTHH:MM)",
                                   placeholder="2026-12-03T18:00")
        maint_desc = gr.Textbox(label="Description")
        with gr.Row():
            maint_add_btn = gr.Button("Add maintenance block", variant="primary")
            maint_del_id = gr.Number(label="Block ID to delete", precision=0)
            maint_del_btn = gr.Button("Delete block")

        def _maint_md():
            rows = []
            for m in halls.list_maintenance(_system_employee()):
                hall = next((h for h in halls.list_halls(_system_employee())
                             if h.id == m.hall_id), None)
                rows.append([m.id, hall.name if hall else m.hall_id, m.type,
                             m.start_at.strftime("%Y-%m-%d %H:%M"),
                             m.end_at.strftime("%Y-%m-%d %H:%M"), m.description])
            return _md_table(["ID", "Hall", "Type", "Start", "End", "Description"], rows)

        def do_refresh_maint(token):
            employee(token)
            return _maint_md()

        def do_add_maint(token, hid, type_, start, end, desc):
            emp = employee(token)
            try:
                halls.add_maintenance(
                    emp, int(hid), type_=type_, description=desc or "",
                    start_at=parse_iso_datetime(start, "Start"),
                    end_at=parse_iso_datetime(end, "End"))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Maintenance block added.")
            return _maint_md()

        def do_del_maint(token, bid):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a block ID first.")
            try:
                halls.delete_maintenance(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Maintenance block deleted.")
            return _maint_md()

        refresh_maint_btn.click(do_refresh_maint, inputs=[session_token], outputs=[maint_list])
        maint_add_btn.click(do_add_maint, inputs=[session_token, maint_hall,
                            maint_type, maint_start, maint_end, maint_desc],
                            outputs=[maint_list])
        maint_del_btn.click(do_del_maint, inputs=[session_token, maint_del_id],
                            outputs=[maint_list])

    # ========================================================= EVENT TYPES
    with gr.Tab("Event Types"):
        gr.Markdown("### Event types (Wedding, Engagement, Birthday, Graduation, Private Events)")
        et_list = gr.Markdown("")
        refresh_et_btn = gr.Button("Refresh event types")
        with gr.Row():
            et_id = gr.Number(label="Event type ID (empty = create new)", precision=0)
            et_name = gr.Textbox(label="Name")
            et_duration = gr.Number(label="Default duration (minutes)", precision=0, value=120)
            et_active = gr.Checkbox(label="Active", value=True)
        et_save_btn = gr.Button("Save event type", variant="primary")

        def _et_md():
            rows = [[t.id, t.name, t.default_duration_min,
                     "yes" if t.is_active else "no"]
                    for t in halls.list_event_types(_system_employee())]
            return _md_table(["ID", "Name", "Default duration (min)", "Active"], rows)

        def do_refresh_et(token):
            employee(token)
            return _et_md()

        def do_save_et(token, tid, name, duration, active):
            emp = employee(token)
            try:
                if tid:
                    halls.update_event_type(emp, int(tid), name=name,
                                            default_duration_min=int(duration),
                                            is_active=bool(active))
                    gr.Info("Event type updated.")
                else:
                    halls.create_event_type(emp, name=name,
                                            default_duration_min=int(duration))
                    gr.Info("Event type created.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _et_md()

        refresh_et_btn.click(do_refresh_et, inputs=[session_token], outputs=[et_list])
        et_save_btn.click(do_save_et, inputs=[session_token, et_id, et_name,
                          et_duration, et_active], outputs=[et_list])

    # ============================================================= PACKAGES
    with gr.Tab("Packages"):
        gr.Markdown("### Packages — base price, durations, included guests, suitable halls / event types / included services")
        pkg_list = gr.Markdown("")
        refresh_pkg_btn = gr.Button("Refresh packages")
        with gr.Row():
            pkg_id = gr.Number(label="Package ID (empty = create new)", precision=0)
            pkg_name = gr.Textbox(label="Name")
            pkg_price = gr.Number(label="Base price (EGP)")
        pkg_desc = gr.Textbox(label="Description")
        with gr.Row():
            pkg_default_dur = gr.Number(label="Default duration (min)", precision=0, value=120)
            pkg_min_dur = gr.Number(label="Minimum duration (min)", precision=0, value=60)
            pkg_guests = gr.Number(label="Included guests", precision=0, value=0)
            pkg_extra = gr.Number(label="Extra guest price (EGP)")
            pkg_active = gr.Checkbox(label="Active", value=True)
        with gr.Row():
            pkg_halls = gr.CheckboxGroup(choices=_hall_choices(), label="Suitable halls")
            pkg_types = gr.CheckboxGroup(
                choices=[(t.name, t.id) for t in halls.list_event_types(_system_employee())],
                label="Suitable event types")
            pkg_services = gr.CheckboxGroup(
                choices=[(s.name, s.id) for s in catalog.list_services(_system_employee())],
                label="Included services (not charged again)")
        pkg_save_btn = gr.Button("Save package", variant="primary")

        def _pkg_md():
            rows = []
            for p in catalog.list_packages(_system_employee()):
                rows.append([p.id, p.name, format_money(p.base_price),
                             f"{p.min_duration_min}–{p.default_duration_min} min",
                             p.included_guests, format_money(p.extra_guest_price),
                             "yes" if p.is_active else "no"])
            return _md_table(["ID", "Name", "Base price", "Duration", "Included guests",
                              "Extra guest", "Active"], rows)

        def do_refresh_pkg(token):
            employee(token)
            return _pkg_md()

        def do_save_pkg(token, pid, name, price, desc, default_dur, min_dur,
                        guests, extra, active, hall_ids, type_ids, service_ids):
            emp = employee(token)
            try:
                base = egp_to_piasters(price, "Base price (EGP)")
                extra_p = egp_to_piasters(extra or 0, "Extra guest price (EGP)")
                kwargs = dict(
                    name=name, description=desc or "", base_price=base,
                    default_duration_min=int(default_dur), min_duration_min=int(min_dur),
                    included_guests=int(guests), extra_guest_price=extra_p,
                    hall_ids=[int(x) for x in hall_ids],
                    event_type_ids=[int(x) for x in type_ids],
                    service_ids=[int(x) for x in service_ids])
                if pid:
                    catalog.update_package(emp, int(pid), is_active=bool(active), **kwargs)
                    gr.Info("Package updated.")
                else:
                    catalog.create_package(emp, **kwargs)
                    gr.Info("Package created.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _pkg_md()

        refresh_pkg_btn.click(do_refresh_pkg, inputs=[session_token], outputs=[pkg_list])
        pkg_save_btn.click(do_save_pkg, inputs=[session_token, pkg_id, pkg_name,
                           pkg_price, pkg_desc, pkg_default_dur, pkg_min_dur,
                           pkg_guests, pkg_extra, pkg_active, pkg_halls, pkg_types,
                           pkg_services], outputs=[pkg_list])

    # ============================================================= SERVICES
    with gr.Tab("Services"):
        gr.Markdown("### Service catalog — pricing method: per event / per hour / per guest / by level")
        svc_list = gr.Markdown("")
        refresh_svc_btn = gr.Button("Refresh services")
        with gr.Row():
            svc_id = gr.Number(label="Service ID (empty = create new)", precision=0)
            svc_name = gr.Textbox(label="Name")
            svc_method = gr.Dropdown(["per_event", "per_hour", "per_guest", "by_level"],
                                     value="per_event", label="Pricing method")
            svc_price = gr.Number(label="Unit price (EGP)")
            svc_active = gr.Checkbox(label="Active", value=True)
        svc_desc = gr.Textbox(label="Description")
        svc_halls = gr.CheckboxGroup(choices=_hall_choices(),
                                     label="Offered in halls (empty = all halls)")
        svc_save_btn = gr.Button("Save service", variant="primary")

        def _svc_md():
            rows = [[s.id, s.name, s.pricing_method, format_money(s.unit_price),
                     "yes" if s.is_active else "no"]
                    for s in catalog.list_services(_system_employee())]
            return _md_table(["ID", "Name", "Pricing method", "Unit price", "Active"], rows)

        def do_refresh_svc(token):
            employee(token)
            return _svc_md()

        def do_save_svc(token, sid, name, method, price, active, desc, hall_ids):
            emp = employee(token)
            try:
                unit = egp_to_piasters(price, "Unit price (EGP)")
                if sid:
                    catalog.update_service(emp, int(sid), name=name, description=desc or "",
                                           pricing_method=method, unit_price=unit,
                                           is_active=bool(active),
                                           hall_ids=[int(x) for x in hall_ids])
                    gr.Info("Service updated.")
                else:
                    catalog.create_service(emp, name=name, description=desc or "",
                                           pricing_method=method, unit_price=unit,
                                           hall_ids=[int(x) for x in hall_ids])
                    gr.Info("Service created.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _svc_md()

        refresh_svc_btn.click(do_refresh_svc, inputs=[session_token], outputs=[svc_list])
        svc_save_btn.click(do_save_svc, inputs=[session_token, svc_id, svc_name,
                           svc_method, svc_price, svc_active, svc_desc, svc_halls],
                           outputs=[svc_list])

    # ========================================================= PRICING RULES
    with gr.Tab("Pricing Rules"):
        gr.Markdown("### Dynamic pricing rules — a rule applies when every filled-in criterion matches. "
                    "Positive value = surcharge, negative = reduction.")
        rule_list = gr.Markdown("")
        refresh_rule_btn = gr.Button("Refresh pricing rules")
        with gr.Row():
            rule_id = gr.Number(label="Rule ID (empty = create new)", precision=0)
            rule_name = gr.Textbox(label="Name")
            rule_hall = gr.Dropdown(choices=[("Any hall", None)] + _hall_choices(), label="Hall")
            rule_pkg = gr.Dropdown(
                choices=[("Any package", None)] +
                        [(p.name, p.id) for p in catalog.list_packages(_system_employee())],
                label="Package")
            rule_type = gr.Dropdown(
                choices=[("Any event type", None)] +
                        [(t.name, t.id) for t in halls.list_event_types(_system_employee())],
                label="Event type")
        with gr.Row():
            rule_weekday = gr.Dropdown(
                choices=[("Any day", None)] + list(enumerate(WEEKDAYS)), label="Weekday")
            rule_season = gr.Textbox(label="Season (blank = any)", value="")
            rule_adj_type = gr.Dropdown(["percent", "fixed"], value="percent",
                                        label="Adjustment type")
            rule_adj_value = gr.Number(label="Adjustment value (+ surcharge / − reduction)",
                                       value=10)
            rule_active = gr.Checkbox(label="Active", value=True)
        rule_save_btn = gr.Button("Save pricing rule", variant="primary")

        def _rule_md():
            rows = []
            for r in catalog.list_pricing_rules(_system_employee()):
                rows.append([r.id, r.name, r.hall_id or "any", r.package_id or "any",
                             r.event_type_id or "any",
                             WEEKDAYS[r.weekday] if r.weekday is not None else "any",
                             r.season or "any", r.adjustment_type,
                             f"{r.adjustment_value:+d}",
                             "yes" if r.is_active else "no"])
            return _md_table(["ID", "Name", "Hall", "Package", "Event type", "Weekday",
                              "Season", "Type", "Value", "Active"], rows)

        def do_refresh_rule(token):
            employee(token)
            return _rule_md()

        def do_save_rule(token, rid, name, hall, pkg, etype, weekday, season,
                         adj_type, adj_value, active):
            emp = employee(token)
            try:
                kwargs = dict(
                    name=name, hall_id=int(hall) if hall else None,
                    package_id=int(pkg) if pkg else None,
                    event_type_id=int(etype) if etype else None,
                    weekday=int(weekday) if weekday is not None and weekday != "" else None,
                    season=season or "", adjustment_type=adj_type,
                    adjustment_value=int(adj_value))
                if rid:
                    catalog.update_pricing_rule(emp, int(rid), is_active=bool(active), **kwargs)
                    gr.Info("Pricing rule updated.")
                else:
                    catalog.create_pricing_rule(emp, **kwargs)
                    gr.Info("Pricing rule created.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _rule_md()

        refresh_rule_btn.click(do_refresh_rule, inputs=[session_token], outputs=[rule_list])
        rule_save_btn.click(do_save_rule, inputs=[session_token, rule_id, rule_name,
                            rule_hall, rule_pkg, rule_type, rule_weekday, rule_season,
                            rule_adj_type, rule_adj_value, rule_active], outputs=[rule_list])

    # ===================================================== PRICE CALCULATOR
    with gr.Tab("Price Calculator"):
        gr.Markdown("### Price preview — itemised breakdown, no booking is created")
        with gr.Row():
            calc_hall = gr.Dropdown(choices=_hall_choices(), label="Hall")
            calc_pkg = gr.Dropdown(
                choices=[("No package", None)] +
                        [(p.name, p.id) for p in catalog.list_packages(_system_employee())],
                label="Package")
            calc_type = gr.Dropdown(
                choices=[("Any", None)] +
                        [(t.name, t.id) for t in halls.list_event_types(_system_employee())],
                label="Event type")
        with gr.Row():
            calc_date = gr.Textbox(label="Event date (YYYY-MM-DD)", placeholder="2026-12-04")
            calc_start = gr.Textbox(label="Start time (HH:MM)", value="18:00")
            calc_end = gr.Textbox(label="End time (HH:MM)", value="23:00")
            calc_guests = gr.Number(label="Guests", precision=0, value=100)
        calc_services = gr.CheckboxGroup(
            choices=[(s.name, s.id) for s in catalog.list_services(_system_employee())],
            label="Additional services (quantity 1; per-hour uses the event duration)")
        with gr.Row():
            calc_disc_type = gr.Dropdown(["none", "fixed", "percent"], value="none",
                                         label="Discount type")
            calc_disc_value = gr.Number(label="Discount value (EGP or %)", value=0)
            calc_disc_reason = gr.Textbox(label="Discount reason (required if discount)")
        calc_btn = gr.Button("Calculate price", variant="primary")
        calc_out = gr.Markdown("")
        avail_out = gr.Markdown("")

        def do_calculate(token, hall, pkg, etype, date, start, end, guests,
                         service_ids, disc_type, disc_value, disc_reason):
            emp = employee(token)
            try:
                start_dt = parse_iso_datetime(f"{date}T{start}:00", "Start")
                end_dt = parse_iso_datetime(f"{date}T{end}:00", "End")
                duration = int((end_dt - start_dt).total_seconds() // 60)
                discount = None
                if disc_type != "none":
                    if disc_type == "fixed":
                        value = egp_to_piasters(disc_value, "Discount (EGP)")
                    else:
                        value = int(disc_value)
                    discount = {"type": disc_type, "value": value,
                                "reason": disc_reason or ""}
                breakdown = pricing.calculate(
                    emp, hall_id=int(hall),
                    package_id=int(pkg) if pkg else None,
                    event_type_id=int(etype) if etype else None,
                    start_at=start_dt, duration_min=duration,
                    guests=int(guests),
                    service_selections=[ServiceSelection(service_id=int(sid))
                                        for sid in service_ids],
                    discount=discount)
                avail = availability.check_availability(
                    emp, hall_id=int(hall), start_at=start_dt, end_at=end_dt)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lines = ["| Item | Amount |", "| --- | ---:|"]
            for item in breakdown.items:
                lines.append(f"| {item.label} | {format_money(item.amount)} |")
            lines.append(f"| **Total** | **{format_money(breakdown.total)}** |")
            avail_md = ("**Availability:** available for that time."
                        if avail else f"**Availability:** NOT available — {avail.reason}")
            return "\n".join(lines), avail_md

        calc_btn.click(do_calculate, inputs=[session_token, calc_hall, calc_pkg,
                       calc_type, calc_date, calc_start, calc_end, calc_guests,
                       calc_services, calc_disc_type, calc_disc_value,
                       calc_disc_reason], outputs=[calc_out, avail_out])
