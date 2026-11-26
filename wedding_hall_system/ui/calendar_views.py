"""Calendar screens (Phase 7, presentation tier, SRS §9; Phase 8:
fully bilingual, SRS §16.3).

Thin UI only: parse input, call ONE service method, render the
result. Unified view for all halls + per-hall view; Day / Week /
Month are date ranges. The table shows bookings, active Pending
holds, maintenance blocks and closures/special hours. Changing a
booking's time from here runs the SAME rescheduling workflow as the
Bookings screen (full revalidation) — Gradio has no drag-and-drop
calendar (GAP G-13, decision A-61).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import catalog_repository as catalog_repo
from ..logic.auth_service import AuthService
from ..logic.calendar_service import RANGE_PRESETS, CalendarService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import LabelRegistry, lang_for_token, t

logger = get_logger(__name__)

BOOKING_STATUS_KEYS = {
    "pending": "booking.status_pending",
    "confirmed": "booking.status_confirmed",
    "in_progress": "booking.status_in_progress",
    "completed": "booking.status_completed",
    "cancelled": "booking.status_cancelled",
    "no_show": "booking.status_no_show",
    "expired": "booking.status_expired",
}
KIND_KEYS = {
    "booking": "cal.kind_booking",
    "maintenance": "cal.kind_maintenance",
    "closure": "cal.kind_closure",
    "special_hours": "cal.kind_special_hours",
}


def _events_md(events: list[dict], lang: str) -> str:
    if not events:
        return f"_{t(lang, 'cal.msg_no_events')}_"
    out = [f"| {t(lang, 'cal.table_kind')} | {t(lang, 'cal.table_event')} | "
           f"{t(lang, 'cal.table_hall')} | {t(lang, 'cal.table_start')} | "
           f"{t(lang, 'cal.table_end')} | {t(lang, 'cal.table_status')} |",
           "| --- | --- | --- | --- | --- | --- |"]
    for e in events:
        status = e["status"]
        if e["kind"] == "booking":
            status = t(lang, BOOKING_STATUS_KEYS.get(status, "booking.status_pending"))
        elif status == "maintenance":
            status = t(lang, "cal.status_maintenance")
        elif status == "closed":
            status = t(lang, "cal.status_closed")
        elif status == "special hours":
            status = t(lang, "cal.kind_special_hours")
        out.append(
            f"| {t(lang, KIND_KEYS.get(e['kind'], 'cal.kind_booking'))} | "
            f"{e['title']} | {e['hall']} | "
            f"{str(e['start'])[:16]} | {str(e['end'])[:16]} | {status} |")
    return "\n".join(out)


def _detail_md(detail: dict, lang: str) -> str:
    def money(value):
        if value is None:
            return t(lang, "cal.no_permission")
        return f"{value // 100:,}.{value % 100:02d} EGP"
    return (
        f"**{t(lang, 'cal.table_booking')} {detail['number']}** — "
        f"{t(lang, BOOKING_STATUS_KEYS.get(detail['status'], 'booking.status_pending'))}  \n"
        f"{t(lang, 'cal.detail_customer')}: {detail['customer']}  \n"
        f"{t(lang, 'cal.detail_hall')}: {detail['hall']}  \n"
        f"{t(lang, 'cal.detail_event_type')}: {detail['event_type'] or '—'} | "
        f"{t(lang, 'cal.detail_package')}: {detail['package'] or '—'}  \n"
        f"{t(lang, 'cal.detail_when')}: {detail['start'][:16]} → {detail['end'][:16]}  \n"
        f"{t(lang, 'cal.detail_guests')}: {detail['guests']}  \n"
        f"{t(lang, 'cal.detail_total')}: {money(detail['total'])}  \n"
        f"{t(lang, 'cal.detail_paid')}: {money(detail['paid'])} | "
        f"{t(lang, 'cal.detail_outstanding')}: {money(detail['outstanding'])}"
    )


def build_calendar_ui(db: DatabaseManager, auth: AuthService,
                      settings: AppSettings, session_token) -> dict:
    """Build the Calendar tab. Returns the LabelRegistry result for
    the language toggle."""
    reg = LabelRegistry()
    calendar = CalendarService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    def _hall_choices(lang: str) -> list[tuple[str, str]]:
        halls = catalog_repo.list_halls(db, include_inactive=False)
        return [(t(lang, "cal.all_halls"), "")] + [
            (h["name"], str(h["id"])) for h in halls]

    with reg.tab("tab.calendar"):
        reg.markdown("cal.heading")
        reg.markdown("cal.help")
        with gr.Row():
            cal_date = reg.textbox("cal.start_date", value=date.today().isoformat())
            cal_range = reg.radio_t(
                "cal.range",
                [("cal.range_day", "day"),
                 ("cal.range_week", "week"),
                 ("cal.range_month", "month")],
                value="week")
            cal_hall = reg.dropdown_data("cal.hall", _hall_choices)
            cal_btn = reg.button("cal.show", variant="primary")
        events_md = gr.Markdown("")

        reg.markdown("cal.detail_heading")
        with gr.Row():
            det_id = reg.number("cal.det_id", precision=0)
            det_btn = reg.button("cal.show_detail")
        detail_md = gr.Markdown("")

        reg.markdown("cal.reschedule_heading")
        with gr.Row():
            rs_id = reg.number("cal.rs_id", precision=0)
            rs_date = reg.textbox("cal.rs_date")
            rs_start = reg.textbox("cal.rs_start", placeholder="18:00")
            rs_end = reg.textbox("cal.rs_end", placeholder="23:00")
            rs_reason = reg.textbox("cal.rs_reason")
            rs_btn = reg.button("cal.rs_btn")
        rs_msg = gr.Markdown("")

        def do_show(token, start_date, range_name, hall_value):
            emp = employee(token)
            lang = _lang(token)
            try:
                days = RANGE_PRESETS[range_name]
                start = datetime.strptime(
                    (start_date or "").strip(), "%Y-%m-%d").date()
                end = start + timedelta(days=days - 1)
                events = calendar.list_events(
                    emp, date_from=start.isoformat(), date_to=end.isoformat(),
                    hall_id=int(hall_value) if hall_value else None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            except ValueError:
                raise gr.Error(t(lang, "cal.msg_bad_date"))
            return (_events_md(events, lang),
                    gr.update(choices=_hall_choices(lang)))

        def do_detail(token, booking_id):
            emp = employee(token)
            lang = _lang(token)
            if not booking_id:
                raise gr.Error(t(lang, "cal.msg_need_booking_id"))
            try:
                return _detail_md(
                    calendar.booking_detail(emp, int(booking_id)), lang)
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_reschedule(token, booking_id, new_date, start_time,
                           end_time, reason):
            emp = employee(token)
            lang = _lang(token)
            if not booking_id:
                raise gr.Error(t(lang, "cal.msg_need_booking_id"))
            try:
                start_dt = datetime.strptime(
                    f"{(new_date or '').strip()} {(start_time or '').strip()}",
                    "%Y-%m-%d %H:%M")
                end_dt = datetime.strptime(
                    f"{(new_date or '').strip()} {(end_time or '').strip()}",
                    "%Y-%m-%d %H:%M")
                booking = calendar.reschedule_from_calendar(
                    emp, int(booking_id), new_start_at=start_dt,
                    new_end_at=end_dt, reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            except ValueError:
                raise gr.Error(t(lang, "cal.msg_bad_datetime"))
            gr.Info(t(lang, "cal.msg_rescheduled").format(number=booking.number))
            return t(lang, "cal.msg_rescheduled_out").format(
                number=booking.number, start=booking.start_at,
                end=booking.end_at)

        cal_btn.click(do_show,
                      inputs=[session_token, cal_date, cal_range, cal_hall],
                      outputs=[events_md, cal_hall])
        det_btn.click(do_detail, inputs=[session_token, det_id],
                      outputs=[detail_md])
        rs_btn.click(do_reschedule,
                     inputs=[session_token, rs_id, rs_date, rs_start, rs_end,
                             rs_reason],
                     outputs=[rs_msg])

    return reg.result()
