"""Halls & pricing screens (Phase 2, presentation tier; Phase 8: fully
bilingual, SRS §16.3).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions; a denied
action raises gr.Error with the service's message.

Money is entered in EGP in the UI and converted to integer piasters
before it reaches a service. All seeded rows are EXAMPLE data — the
screens label them as such.

Language: every visible string comes from ``ui/i18n.py``. Components
are built through ``LabelRegistry`` (Arabic default) and the main
view's toggle renames them; rendered tables/messages use the
handler's session language (``lang_for_token``).
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
from ..utils.exceptions import ApplicationError, HallDeleteBlockedError
from ..utils.logging_config import get_logger
from ..utils.validators import egp_to_piasters, parse_iso_datetime
from .i18n import LabelRegistry, dir_wrap, lang_for_token, t, translate_price_label

logger = get_logger(__name__)

WEEKDAY_KEYS = [
    "catalog.weekday_monday", "catalog.weekday_tuesday",
    "catalog.weekday_wednesday", "catalog.weekday_thursday",
    "catalog.weekday_friday", "catalog.weekday_saturday",
    "catalog.weekday_sunday",
]
STATUS_KEYS = {
    "active": "catalog.status_active",
    "inactive": "catalog.status_inactive",
    "maintenance": "catalog.status_maintenance",
}
METHOD_KEYS = {
    "per_event": "catalog.method_per_event",
    "per_hour": "catalog.method_per_hour",
    "per_guest": "catalog.method_per_guest",
    "by_level": "catalog.method_by_level",
}
ADJ_KEYS = {"percent": "catalog.adj_percent", "fixed": "catalog.adj_fixed"}


def _md_table(headers: list[str], rows: list[list[str]], lang: str) -> str:
    if not rows:
        return dir_wrap(lang, f"_{t(lang, 'common.no_records')}_")
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return dir_wrap(lang, "\n".join(out))


def _yes_no(lang: str, value: bool) -> str:
    return t(lang, "common.yes" if value else "common.no")


def build_catalog_ui(db: DatabaseManager, auth: AuthService,
                     settings: AppSettings, session_token) -> dict:
    """Build the Halls & Pricing tabs. Must be called inside a gr.Blocks
    context, after the login/main views exist. ``session_token`` is the
    gr.State holding the login token. Returns the LabelRegistry result
    so the main view's language toggle can rename everything."""
    reg = LabelRegistry()
    halls = HallService(db, settings)
    catalog = CatalogService(db, settings)
    pricing = PricingService(db, settings)
    availability = AvailabilityService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    def _hall_choices():
        return [(f"{h.name} — {h.capacity}", h.id)
                for h in halls.list_halls(_system_employee(), include_inactive=True)]

    def _system_employee():
        # Read-only list helpers need an employee with view rights; the
        # Owner demo account always exists (seeded). Used only to build
        # dropdown choices — every write still checks the real user.
        row = db.query_one("SELECT id FROM employees WHERE username = 'owner'")
        from ..logic.models import Employee
        return Employee(id=int(row["id"]), username="owner", full_name="Owner")

    # ============================================================== HALLS
    with reg.tab("tab.halls"):
        reg.markdown("catalog.halls_heading")
        halls_list = gr.Markdown("")
        refresh_halls_btn = reg.button("catalog.refresh_halls")
        with gr.Row():
            hall_id = reg.number("catalog.hall_id", precision=0)
            hall_name = reg.textbox("catalog.hall_name")
            hall_capacity = reg.number("catalog.hall_capacity", precision=0)
            hall_price = reg.number("catalog.hall_price")
        with gr.Row():
            hall_status = reg.dropdown_t(
                "catalog.hall_status",
                [("catalog.status_active", "active"),
                 ("catalog.status_inactive", "inactive"),
                 ("catalog.status_maintenance", "maintenance")],
                value="active")
            hall_facilities = reg.textbox(
                "catalog.hall_facilities",
                placeholder_key="catalog.hall_facilities_hint")
            hall_notes = reg.textbox("catalog.hall_notes")
        hall_desc = reg.textbox("catalog.hall_description")
        with gr.Row():
            hall_save_btn = reg.button("catalog.save_hall", variant="primary")
            hall_deactivate_btn = reg.button("catalog.deactivate_hall")
            hall_reactivate_btn = reg.button("catalog.reactivate_hall")
            hall_delete_btn = reg.button("catalog.delete_hall")
        # Two-step delete (A-90): the first click only arms the
        # delete and shows the confirm/cancel buttons + a warning;
        # nothing is deleted until "Confirm delete" is clicked.
        hall_delete_warn = gr.Markdown("")
        with gr.Row():
            hall_delete_confirm_btn = reg.button(
                "catalog.confirm_delete_hall", visible=False)
            hall_delete_cancel_btn = reg.button(
                "common.cancel", visible=False)

        def _halls_md(lang: str):
            rows = []
            for h in halls.list_halls(_system_employee(), include_inactive=True):
                # A-92: the table shows ONE status column (الحالة).
                # The old نشط (is_active) column duplicated it and
                # disagreed with it; is_active stays in the data
                # model and in availability logic, it is just not
                # displayed anymore.
                rows.append([h.id, h.name, h.capacity, format_money(h.base_price),
                             t(lang, STATUS_KEYS.get(h.status, "catalog.status_active")),
                             ", ".join(h.facilities)])
            return dir_wrap(lang, _md_table(
                [t(lang, "catalog.table_id"), t(lang, "catalog.table_name"),
                 t(lang, "catalog.table_capacity"),
                 t(lang, "catalog.table_base_price"),
                 t(lang, "catalog.table_status"),
                 t(lang, "catalog.table_facilities")], rows, lang))

        def do_refresh_halls(token):
            employee(token)
            return _halls_md(_lang(token))

        def do_save_hall(token, hid, name, capacity, price, status,
                         facilities, notes, desc):
            emp = employee(token)
            lang = _lang(token)
            try:
                base = egp_to_piasters(price, "Base price (EGP)")
                facs = [f.strip() for f in (facilities or "").split(",") if f.strip()]
                if hid:
                    hall = halls.update_hall(
                        emp, int(hid), name=name, description=desc or "",
                        capacity=int(capacity), base_price=base, status=status,
                        facilities=facs, notes=notes or "")
                    gr.Info(t(lang, "catalog.msg_hall_updated").format(name=hall.name))
                else:
                    hall = halls.create_hall(
                        emp, name=name, description=desc or "",
                        capacity=int(capacity), base_price=base, status=status,
                        facilities=facs, notes=notes or "")
                    gr.Info(t(lang, "catalog.msg_hall_created").format(name=hall.name))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _halls_md(lang)

        def do_set_hall_active(token, hid, active: bool):
            emp = employee(token)
            lang = _lang(token)
            if not hid:
                raise gr.Error(t(lang, "catalog.msg_need_hall_id"))
            try:
                hall = halls.set_hall_active(emp, int(hid), active)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "catalog.msg_hall_active").format(
                name=hall.name,
                state=t(lang, "catalog.status_active" if active else "catalog.status_inactive")))
            return _halls_md(lang)

        def do_arm_delete_hall(token, hid):
            """First step of the two-step delete (A-90): show the
            confirm/cancel buttons and a warning naming the hall.
            Nothing is deleted here; the permission check happens on
            confirm, in the service."""
            emp = employee(token)
            lang = _lang(token)
            if not hid:
                raise gr.Error(t(lang, "catalog.msg_need_hall_id"))
            try:
                hall = halls.get_hall(emp, int(hid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return (gr.update(visible=True), gr.update(visible=True),
                    dir_wrap(lang, t(lang, "catalog.msg_hall_delete_confirm").format(
                        name=hall.name)))

        def do_delete_hall(token, hid):
            """Second step: permanently delete, or refuse with a
            bilingual message when preserved records block it."""
            emp = employee(token)
            lang = _lang(token)
            if not hid:
                raise gr.Error(t(lang, "catalog.msg_need_hall_id"))
            try:
                name = halls.delete_hall(emp, int(hid))
            except HallDeleteBlockedError as exc:
                raise gr.Error(t(lang, "catalog.msg_hall_delete_blocked").format(
                    name=exc.hall_name, bookings=exc.bookings,
                    quotations=exc.quotations, history=exc.history,
                    expenses=exc.expenses))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "catalog.msg_hall_deleted").format(name=name))
            return (_halls_md(lang), gr.update(visible=False),
                    gr.update(visible=False), "")

        def do_cancel_delete_hall():
            return (gr.update(visible=False), gr.update(visible=False), "")

        def do_refresh_halls_on_login(token):
            """Login/restore follow-up (A-91): populate the halls list
            right after sign-in, so the table is already filled when
            the user opens the site. Anonymous page loads (no valid
            session) leave the list untouched instead of raising."""
            if auth.validate_session(token) is None:
                return gr.update()
            return do_refresh_halls(token)

        refresh_halls_btn.click(do_refresh_halls, inputs=[session_token], outputs=[halls_list])
        hall_save_btn.click(do_save_hall, inputs=[session_token, hall_id, hall_name,
                            hall_capacity, hall_price, hall_status, hall_facilities,
                            hall_notes, hall_desc], outputs=[halls_list])
        hall_deactivate_btn.click(lambda t, i: do_set_hall_active(t, i, False),
                                  inputs=[session_token, hall_id], outputs=[halls_list])
        hall_reactivate_btn.click(lambda t, i: do_set_hall_active(t, i, True),
                                  inputs=[session_token, hall_id], outputs=[halls_list])
        hall_delete_btn.click(do_arm_delete_hall, inputs=[session_token, hall_id],
                              outputs=[hall_delete_confirm_btn, hall_delete_cancel_btn,
                                       hall_delete_warn])
        hall_delete_confirm_btn.click(do_delete_hall, inputs=[session_token, hall_id],
                                      outputs=[halls_list, hall_delete_confirm_btn,
                                               hall_delete_cancel_btn, hall_delete_warn])
        hall_delete_cancel_btn.click(do_cancel_delete_hall, inputs=[],
                                     outputs=[hall_delete_confirm_btn,
                                              hall_delete_cancel_btn,
                                              hall_delete_warn])

    # ============================================================== HOURS
    with reg.tab("tab.hours"):
        reg.markdown("catalog.hours_heading")
        hours_list = gr.Markdown("")
        refresh_hours_btn = reg.button("catalog.refresh_hours")
        with gr.Row():
            hours_hall = reg.dropdown("catalog.calc_hall", choices=_hall_choices())
            hours_weekday = reg.dropdown_data(
                "catalog.weekday",
                lambda lang: [(t(lang, WEEKDAY_KEYS[i]), i) for i in range(7)])
            hours_open = reg.textbox("catalog.opens", value="12:00")
            hours_close = reg.textbox("catalog.closes", value="23:00")
            hours_closed = reg.checkbox("catalog.closed_that_day", value=False)
        hours_set_btn = reg.button("catalog.save_hours", variant="primary")
        reg.markdown("catalog.special_heading")
        with gr.Row():
            sp_hall = reg.dropdown("catalog.calc_hall", choices=_hall_choices())
            sp_date = reg.textbox("catalog.special_date", placeholder="2026-12-25")
            sp_open = reg.textbox("catalog.special_open", value="12:00")
            sp_close = reg.textbox("catalog.special_close", value="23:00")
            sp_closed = reg.checkbox("catalog.special_closed", value=False)
            sp_reason = reg.textbox("catalog.special_reason")
        with gr.Row():
            sp_add_btn = reg.button("catalog.add_special", variant="primary")
            sp_del_id = reg.number("catalog.special_id_delete", precision=0)
            sp_del_btn = reg.button("catalog.delete_special")

        def _hours_md(lang: str):
            lines = []
            for h in halls.list_halls(_system_employee(), include_inactive=True):
                lines.append(f"**{h.name}** (ID {h.id})")
                weekly = halls.list_weekly_hours(_system_employee(), h.id)
                wrows = [[t(lang, WEEKDAY_KEYS[w.weekday]),
                          t(lang, "common.closed") if w.is_closed else f"{w.open_time}–{w.close_time}"]
                         for w in weekly]
                lines.append(_md_table(
                    [t(lang, "catalog.table_weekday"),
                     t(lang, "catalog.table_hours")], wrows, lang))
                special = halls.list_special_hours(_system_employee(), h.id)
                srows = [[s.id, s.date,
                          t(lang, "common.closed") if s.is_closed else f"{s.open_time}–{s.close_time}",
                          s.reason] for s in special]
                lines.append(_md_table(
                    [t(lang, "catalog.table_id"), t(lang, "catalog.table_date"),
                     t(lang, "catalog.table_hours"),
                     t(lang, "catalog.table_reason")], srows, lang))
            return dir_wrap(lang, "\n\n".join(lines))

        def do_refresh_hours(token):
            employee(token)
            return _hours_md(_lang(token))

        def do_set_hours(token, hid, weekday, open_t, close_t, closed):
            emp = employee(token)
            lang = _lang(token)
            try:
                halls.set_weekly_hours(emp, int(hid), weekday=int(weekday),
                                       open_time=open_t or "00:00",
                                       close_time=close_t or "00:00",
                                       is_closed=bool(closed))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "catalog.msg_hours_saved"))
            return _hours_md(lang)

        def do_add_special(token, hid, date, open_t, close_t, closed, reason):
            emp = employee(token)
            lang = _lang(token)
            try:
                halls.add_special_hours(
                    emp, int(hid), date=date,
                    open_time=None if closed else (open_t or None),
                    close_time=None if closed else (close_t or None),
                    is_closed=bool(closed), reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "catalog.msg_special_added"))
            return _hours_md(lang)

        def do_del_special(token, sid):
            emp = employee(token)
            lang = _lang(token)
            if not sid:
                raise gr.Error(t(lang, "catalog.msg_need_special_id"))
            try:
                halls.delete_special_hours(emp, int(sid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "catalog.msg_special_deleted"))
            return _hours_md(lang)

        refresh_hours_btn.click(do_refresh_hours, inputs=[session_token], outputs=[hours_list])
        hours_set_btn.click(do_set_hours, inputs=[session_token, hours_hall,
                            hours_weekday, hours_open, hours_close, hours_closed],
                            outputs=[hours_list])
        sp_add_btn.click(do_add_special, inputs=[session_token, sp_hall, sp_date,
                         sp_open, sp_close, sp_closed, sp_reason], outputs=[hours_list])
        sp_del_btn.click(do_del_special, inputs=[session_token, sp_del_id], outputs=[hours_list])

    # ========================================================== MAINTENANCE
    with reg.tab("tab.maintenance"):
        reg.markdown("catalog.maint_heading")
        maint_list = gr.Markdown("")
        refresh_maint_btn = reg.button("catalog.refresh_maintenance")
        with gr.Row():
            maint_hall = reg.dropdown("catalog.calc_hall", choices=_hall_choices())
            maint_type = reg.textbox("catalog.maint_type",
                                     placeholder_key="catalog.maint_type_hint")
            maint_start = reg.textbox("catalog.maint_start",
                                      placeholder="2026-12-01T09:00")
            maint_end = reg.textbox("catalog.maint_end",
                                    placeholder="2026-12-03T18:00")
        maint_desc = reg.textbox("catalog.maint_description")
        with gr.Row():
            maint_add_btn = reg.button("catalog.add_maintenance", variant="primary")
            maint_del_id = reg.number("catalog.maint_id_delete", precision=0)
            maint_del_btn = reg.button("catalog.delete_maintenance")

        def _maint_md(lang: str):
            rows = []
            for m in halls.list_maintenance(_system_employee()):
                hall = next((h for h in halls.list_halls(_system_employee())
                             if h.id == m.hall_id), None)
                rows.append([m.id, hall.name if hall else m.hall_id, m.type,
                             m.start_at.strftime("%Y-%m-%d %H:%M"),
                             m.end_at.strftime("%Y-%m-%d %H:%M"), m.description])
            return dir_wrap(lang, _md_table(
                [t(lang, "catalog.table_id"), t(lang, "catalog.table_hall"),
                 t(lang, "catalog.table_type"), t(lang, "catalog.table_start"),
                 t(lang, "catalog.table_end"),
                 t(lang, "catalog.table_description")], rows, lang))

        def do_refresh_maint(token):
            employee(token)
            return _maint_md(_lang(token))

        def do_add_maint(token, hid, type_, start, end, desc):
            emp = employee(token)
            lang = _lang(token)
            try:
                halls.add_maintenance(
                    emp, int(hid), type_=type_, description=desc or "",
                    start_at=parse_iso_datetime(start, "Start"),
                    end_at=parse_iso_datetime(end, "End"))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "catalog.msg_maint_added"))
            return _maint_md(lang)

        def do_del_maint(token, bid):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "catalog.msg_need_block_id"))
            try:
                halls.delete_maintenance(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "catalog.msg_maint_deleted"))
            return _maint_md(lang)

        refresh_maint_btn.click(do_refresh_maint, inputs=[session_token], outputs=[maint_list])
        maint_add_btn.click(do_add_maint, inputs=[session_token, maint_hall,
                            maint_type, maint_start, maint_end, maint_desc],
                            outputs=[maint_list])
        maint_del_btn.click(do_del_maint, inputs=[session_token, maint_del_id],
                            outputs=[maint_list])

    # ========================================================= EVENT TYPES
    with reg.tab("tab.event_types"):
        reg.markdown("catalog.event_types_heading")
        et_list = gr.Markdown("")
        refresh_et_btn = reg.button("catalog.refresh_event_types")
        with gr.Row():
            et_id = reg.number("catalog.event_type_id", precision=0)
            et_name = reg.textbox("catalog.event_type_name")
            et_duration = reg.number("catalog.event_type_duration", precision=0, value=120)
            et_active = reg.checkbox("catalog.event_type_active", value=True)
        et_save_btn = reg.button("catalog.save_event_type", variant="primary")

        def _et_md(lang: str):
            rows = [[t.id, t.name, t.default_duration_min,
                     _yes_no(lang, t.is_active)]
                    for t in halls.list_event_types(_system_employee())]
            return dir_wrap(lang, _md_table(
                [t(lang, "catalog.table_id"), t(lang, "catalog.table_name"),
                 t(lang, "catalog.table_duration"),
                 t(lang, "catalog.table_active")], rows, lang))

        def do_refresh_et(token):
            employee(token)
            return _et_md(_lang(token))

        def do_save_et(token, tid, name, duration, active):
            emp = employee(token)
            lang = _lang(token)
            try:
                if tid:
                    halls.update_event_type(emp, int(tid), name=name,
                                            default_duration_min=int(duration),
                                            is_active=bool(active))
                    gr.Info(t(lang, "catalog.msg_event_type_updated"))
                else:
                    halls.create_event_type(emp, name=name,
                                            default_duration_min=int(duration))
                    gr.Info(t(lang, "catalog.msg_event_type_created"))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _et_md(lang)

        refresh_et_btn.click(do_refresh_et, inputs=[session_token], outputs=[et_list])
        et_save_btn.click(do_save_et, inputs=[session_token, et_id, et_name,
                          et_duration, et_active], outputs=[et_list])

    # ============================================================= PACKAGES
    with reg.tab("tab.packages"):
        reg.markdown("catalog.packages_heading")
        pkg_list = gr.Markdown("")
        refresh_pkg_btn = reg.button("catalog.refresh_packages")
        with gr.Row():
            pkg_id = reg.number("catalog.package_id", precision=0)
            pkg_name = reg.textbox("catalog.package_name")
            pkg_price = reg.number("catalog.package_price")
        pkg_desc = reg.textbox("catalog.package_description")
        with gr.Row():
            pkg_default_dur = reg.number("catalog.package_default_duration", precision=0, value=120)
            pkg_min_dur = reg.number("catalog.package_min_duration", precision=0, value=60)
            pkg_guests = reg.number("catalog.package_guests", precision=0, value=0)
            pkg_extra = reg.number("catalog.package_extra_price")
            pkg_active = reg.checkbox("catalog.package_active", value=True)
        with gr.Row():
            pkg_halls = reg.checkbox_group("catalog.package_halls", choices=_hall_choices())
            pkg_types = reg.checkbox_group(
                "catalog.package_types",
                choices=[(t.name, t.id) for t in halls.list_event_types(_system_employee())])
            pkg_services = reg.checkbox_group(
                "catalog.package_services",
                choices=[(s.name, s.id) for s in catalog.list_services(_system_employee())])
        pkg_save_btn = reg.button("catalog.save_package", variant="primary")

        def _pkg_md(lang: str):
            rows = []
            for p in catalog.list_packages(_system_employee()):
                rows.append([p.id, p.name, format_money(p.base_price),
                             f"{p.min_duration_min}–{p.default_duration_min} "
                             f"{t(lang, 'catalog.min_unit')}",
                             p.included_guests, format_money(p.extra_guest_price),
                             _yes_no(lang, p.is_active)])
            return dir_wrap(lang, _md_table(
                [t(lang, "catalog.table_id"), t(lang, "catalog.table_name"),
                 t(lang, "catalog.table_base_price"),
                 t(lang, "catalog.table_duration_range"),
                 t(lang, "catalog.table_guests"),
                 t(lang, "catalog.table_extra"),
                 t(lang, "catalog.table_active")], rows, lang))

        def do_refresh_pkg(token):
            employee(token)
            return _pkg_md(_lang(token))

        def do_save_pkg(token, pid, name, price, desc, default_dur, min_dur,
                        guests, extra, active, hall_ids, type_ids, service_ids):
            emp = employee(token)
            lang = _lang(token)
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
                    gr.Info(t(lang, "catalog.msg_package_updated"))
                else:
                    catalog.create_package(emp, **kwargs)
                    gr.Info(t(lang, "catalog.msg_package_created"))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _pkg_md(lang)

        refresh_pkg_btn.click(do_refresh_pkg, inputs=[session_token], outputs=[pkg_list])
        pkg_save_btn.click(do_save_pkg, inputs=[session_token, pkg_id, pkg_name,
                           pkg_price, pkg_desc, pkg_default_dur, pkg_min_dur,
                           pkg_guests, pkg_extra, pkg_active, pkg_halls, pkg_types,
                           pkg_services], outputs=[pkg_list])

    # ============================================================= SERVICES
    with reg.tab("tab.services"):
        reg.markdown("catalog.services_heading")
        svc_list = gr.Markdown("")
        refresh_svc_btn = reg.button("catalog.refresh_services")
        with gr.Row():
            svc_id = reg.number("catalog.service_id", precision=0)
            svc_name = reg.textbox("catalog.service_name")
            svc_method = reg.dropdown_t(
                "catalog.service_method",
                [("catalog.method_per_event", "per_event"),
                 ("catalog.method_per_hour", "per_hour"),
                 ("catalog.method_per_guest", "per_guest"),
                 ("catalog.method_by_level", "by_level")],
                value="per_event")
            svc_price = reg.number("catalog.service_price")
            svc_active = reg.checkbox("catalog.service_active", value=True)
        svc_desc = reg.textbox("catalog.service_description")
        svc_halls = reg.checkbox_group(
            "catalog.service_halls", choices=_hall_choices())
        svc_save_btn = reg.button("catalog.save_service", variant="primary")

        def _svc_md(lang: str):
            rows = [[s.id, s.name,
                     t(lang, METHOD_KEYS.get(s.pricing_method, "catalog.method_per_event")),
                     format_money(s.unit_price), _yes_no(lang, s.is_active)]
                    for s in catalog.list_services(_system_employee())]
            return dir_wrap(lang, _md_table(
                [t(lang, "catalog.table_id"), t(lang, "catalog.table_name"),
                 t(lang, "catalog.table_method"),
                 t(lang, "catalog.table_unit_price"),
                 t(lang, "catalog.table_active")], rows, lang))

        def do_refresh_svc(token):
            employee(token)
            return _svc_md(_lang(token))

        def do_save_svc(token, sid, name, method, price, active, desc, hall_ids):
            emp = employee(token)
            lang = _lang(token)
            try:
                unit = egp_to_piasters(price, "Unit price (EGP)")
                if sid:
                    catalog.update_service(emp, int(sid), name=name, description=desc or "",
                                           pricing_method=method, unit_price=unit,
                                           is_active=bool(active),
                                           hall_ids=[int(x) for x in hall_ids])
                    gr.Info(t(lang, "catalog.msg_service_updated"))
                else:
                    catalog.create_service(emp, name=name, description=desc or "",
                                           pricing_method=method, unit_price=unit,
                                           hall_ids=[int(x) for x in hall_ids])
                    gr.Info(t(lang, "catalog.msg_service_created"))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _svc_md(lang)

        refresh_svc_btn.click(do_refresh_svc, inputs=[session_token], outputs=[svc_list])
        svc_save_btn.click(do_save_svc, inputs=[session_token, svc_id, svc_name,
                           svc_method, svc_price, svc_active, svc_desc, svc_halls],
                           outputs=[svc_list])

    # ========================================================= PRICING RULES
    with reg.tab("tab.pricing_rules"):
        reg.markdown("catalog.rules_heading")
        rule_list = gr.Markdown("")
        refresh_rule_btn = reg.button("catalog.refresh_rules")
        with gr.Row():
            rule_id = reg.number("catalog.rule_id", precision=0)
            rule_name = reg.textbox("catalog.rule_name")
            rule_hall = reg.dropdown_data(
                "catalog.rule_hall",
                lambda lang: [(t(lang, "catalog.any_hall"), None)] + _hall_choices())
            rule_pkg = reg.dropdown_data(
                "catalog.rule_package",
                lambda lang: [(t(lang, "catalog.any_package"), None)] +
                [(p.name, p.id) for p in catalog.list_packages(_system_employee())])
            rule_type = reg.dropdown_data(
                "catalog.rule_event_type",
                lambda lang: [(t(lang, "catalog.any_event_type"), None)] +
                [(t.name, t.id) for t in halls.list_event_types(_system_employee())])
        with gr.Row():
            rule_weekday = reg.dropdown_data(
                "catalog.rule_weekday",
                lambda lang: [(t(lang, "catalog.any_day"), None)] +
                [(t(lang, WEEKDAY_KEYS[i]), i) for i in range(7)])
            rule_season = reg.textbox("catalog.rule_season", value="")
            rule_adj_type = reg.dropdown_t(
                "catalog.rule_adj_type",
                [("catalog.adj_percent", "percent"),
                 ("catalog.adj_fixed", "fixed")],
                value="percent")
            rule_adj_value = reg.number("catalog.rule_adj_value", value=10)
            rule_active = reg.checkbox("catalog.rule_active", value=True)
        rule_save_btn = reg.button("catalog.save_rule", variant="primary")

        def _rule_md(lang: str):
            rows = []
            for r in catalog.list_pricing_rules(_system_employee()):
                rows.append([r.id, r.name, r.hall_id or t(lang, "common.any"),
                             r.package_id or t(lang, "common.any"),
                             r.event_type_id or t(lang, "common.any"),
                             t(lang, WEEKDAY_KEYS[r.weekday]) if r.weekday is not None else t(lang, "common.any"),
                             r.season or t(lang, "common.any"),
                             t(lang, ADJ_KEYS.get(r.adjustment_type, "catalog.adj_percent")),
                             f"{r.adjustment_value:+d}",
                             _yes_no(lang, r.is_active)])
            return dir_wrap(lang, _md_table(
                [t(lang, "catalog.table_id"), t(lang, "catalog.table_name"),
                 t(lang, "catalog.table_hall"), t(lang, "catalog.table_package"),
                 t(lang, "catalog.table_event_type"),
                 t(lang, "catalog.table_weekday"), t(lang, "catalog.table_season"),
                 t(lang, "catalog.table_type"), t(lang, "catalog.table_value"),
                 t(lang, "catalog.table_active")], rows, lang))

        def do_refresh_rule(token):
            employee(token)
            return _rule_md(_lang(token))

        def do_save_rule(token, rid, name, hall, pkg, etype, weekday, season,
                         adj_type, adj_value, active):
            emp = employee(token)
            lang = _lang(token)
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
                    gr.Info(t(lang, "catalog.msg_rule_updated"))
                else:
                    catalog.create_pricing_rule(emp, **kwargs)
                    gr.Info(t(lang, "catalog.msg_rule_created"))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _rule_md(lang)

        refresh_rule_btn.click(do_refresh_rule, inputs=[session_token], outputs=[rule_list])
        rule_save_btn.click(do_save_rule, inputs=[session_token, rule_id, rule_name,
                            rule_hall, rule_pkg, rule_type, rule_weekday, rule_season,
                            rule_adj_type, rule_adj_value, rule_active], outputs=[rule_list])

    # ===================================================== PRICE CALCULATOR
    with reg.tab("tab.price_calculator"):
        reg.markdown("catalog.calc_heading")
        with gr.Row():
            calc_hall = reg.dropdown("catalog.calc_hall", choices=_hall_choices())
            calc_pkg = reg.dropdown_data(
                "catalog.calc_package",
                lambda lang: [(t(lang, "catalog.no_package"), None)] +
                [(p.name, p.id) for p in catalog.list_packages(_system_employee())])
            calc_type = reg.dropdown_data(
                "catalog.calc_event_type",
                lambda lang: [(t(lang, "catalog.any"), None)] +
                [(t.name, t.id) for t in halls.list_event_types(_system_employee())])
        with gr.Row():
            calc_date = reg.textbox("catalog.calc_date", placeholder="2026-12-04")
            calc_start = reg.textbox("catalog.calc_start", value="18:00")
            calc_end = reg.textbox("catalog.calc_end", value="23:00")
            calc_guests = reg.number("catalog.calc_guests", precision=0, value=100)
        calc_services = reg.checkbox_group(
            "catalog.calc_services",
            choices=[(s.name, s.id) for s in catalog.list_services(_system_employee())])
        with gr.Row():
            calc_disc_type = reg.dropdown_t(
                "catalog.calc_disc_type",
                [("catalog.disc_none", "none"),
                 ("catalog.adj_fixed", "fixed"),
                 ("catalog.adj_percent", "percent")],
                value="none")
            calc_disc_value = reg.number("catalog.calc_disc_value", value=0)
            calc_disc_reason = reg.textbox("catalog.calc_disc_reason")
        calc_btn = reg.button("catalog.calculate", variant="primary")
        calc_out = gr.Markdown("")
        avail_out = gr.Markdown("")

        def do_calculate(token, hall, pkg, etype, date, start, end, guests,
                         service_ids, disc_type, disc_value, disc_reason):
            emp = employee(token)
            lang = _lang(token)
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
                    emp, hall_id=int(hall), start_at=start_dt, end_at=end_dt,
                    lang=lang)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lines = [f"| {t(lang, 'catalog.table_item')} | {t(lang, 'catalog.table_amount')} |",
                     "| --- | ---:|"]
            for item in breakdown.items:
                lines.append(f"| {translate_price_label(lang, item.label)} | {format_money(item.amount)} |")
            lines.append(f"| **{t(lang, 'catalog.table_total')}** | **{format_money(breakdown.total)}** |")
            avail_md = (t(lang, "catalog.msg_avail_yes")
                        if avail else t(lang, "catalog.msg_avail_no").format(reason=avail.reason))
            return dir_wrap(lang, "\n".join(lines)), dir_wrap(lang, avail_md)

        calc_btn.click(do_calculate, inputs=[session_token, calc_hall, calc_pkg,
                       calc_type, calc_date, calc_start, calc_end, calc_guests,
                       calc_services, calc_disc_type, calc_disc_value,
                       calc_disc_reason], outputs=[calc_out, avail_out])

    result = reg.result()
    # A-91: handlers the main view runs right after a successful
    # login / session restore (chained with .then() in app.py), so
    # the halls list is already populated when the user opens the
    # site. (fn, outputs) pairs, mirroring the "renders" pattern.
    result["on_login"] = [(do_refresh_halls_on_login, [halls_list])]
    return result
