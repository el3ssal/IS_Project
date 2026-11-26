"""Calendar screens (Phase 7, presentation tier, SRS §9).

Thin UI only: parse input, call ONE service method, render the
result. Unified view for all halls + per-hall view; Day / Week /
Month are date ranges. The table shows bookings, active Pending
holds, maintenance blocks and closures/special hours. Changing a
booking's time from here runs the SAME rescheduling workflow as the
Bookings screen (full revalidation) — Gradio has no drag-and-drop
calendar (GAP G-13, decision A-61).
"""
from __future__ import annotations

from datetime import date, datetime

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import catalog_repository as catalog_repo
from ..logic.auth_service import AuthService
from ..logic.calendar_service import RANGE_PRESETS, CalendarService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def _events_md(events: list[dict]) -> str:
    if not events:
        return "_No events in this range._"
    out = ["| Kind | Event | Hall | Start | End | Status |",
           "| --- | --- | --- | --- | --- | --- |"]
    for e in events:
        out.append(
            f"| {e['kind']} | {e['title']} | {e['hall']} | "
            f"{str(e['start'])[:16]} | {str(e['end'])[:16]} | {e['status']} |")
    return "\n".join(out)


def _detail_md(detail: dict) -> str:
    def money(value):
        if value is None:
            return "— (no permission)"
        return f"{value // 100:,}.{value % 100:02d} EGP"
    return (
        f"**Booking {detail['number']}** — {detail['status']}  \n"
        f"Customer: {detail['customer']}  \n"
        f"Hall: {detail['hall']}  \n"
        f"Event type: {detail['event_type'] or '—'} | "
        f"Package: {detail['package'] or '—'}  \n"
        f"When: {detail['start'][:16]} → {detail['end'][:16]}  \n"
        f"Guests: {detail['guests']}  \n"
        f"Total: {money(detail['total'])}  \n"
        f"Paid: {money(detail['paid'])} | "
        f"Outstanding: {money(detail['outstanding'])}"
    )


def build_calendar_ui(db: DatabaseManager, auth: AuthService,
                      settings: AppSettings, session_token) -> dict:
    """Build the Calendar tab."""
    calendar = CalendarService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    def hall_choices() -> list[tuple[str, str]]:
        halls = catalog_repo.list_halls(db, include_inactive=False)
        return [("All halls", "")] + [(h["name"], str(h["id"])) for h in halls]

    with gr.Tab("Calendar • التقويم"):
        gr.Markdown("## Calendar — bookings, holds, maintenance, closures (SRS §9)")
        gr.Markdown("Pick a start date and a range. To move a booking, "
                    "use the reschedule form below — it runs the full "
                    "rescheduling workflow (same validation as the "
                    "Bookings screen).")
        with gr.Row():
            cal_date = gr.Textbox(label="Start date (YYYY-MM-DD)",
                                  value=date.today().isoformat())
            cal_range = gr.Radio(list(RANGE_PRESETS), value="week",
                                 label="Range")
            cal_hall = gr.Dropdown(hall_choices(), value="",
                                   label="Hall")
            cal_btn = gr.Button("Show calendar", variant="primary")
        events_md = gr.Markdown("")

        gr.Markdown("### Booking detail")
        with gr.Row():
            det_id = gr.Number(label="Booking ID", precision=0)
            det_btn = gr.Button("Show detail")
        detail_md = gr.Markdown("")

        gr.Markdown("### Reschedule from calendar")
        with gr.Row():
            rs_id = gr.Number(label="Booking ID", precision=0)
            rs_date = gr.Textbox(label="New date (YYYY-MM-DD)")
            rs_start = gr.Textbox(label="New start time (HH:MM, 24h)",
                                  placeholder="18:00")
            rs_end = gr.Textbox(label="New end time (HH:MM, 24h)",
                                placeholder="23:00")
            rs_reason = gr.Textbox(label="Reason")
            rs_btn = gr.Button("Reschedule booking")
        rs_msg = gr.Markdown("")

        def do_show(token, start_date, range_name, hall_value):
            emp = employee(token)
            try:
                days = RANGE_PRESETS[range_name]
                start = datetime.strptime(
                    (start_date or "").strip(), "%Y-%m-%d").date()
                from datetime import timedelta
                end = start + timedelta(days=days - 1)
                events = calendar.list_events(
                    emp, date_from=start.isoformat(), date_to=end.isoformat(),
                    hall_id=int(hall_value) if hall_value else None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            except ValueError:
                raise gr.Error("Start date must be YYYY-MM-DD.")
            return (_events_md(events),
                    gr.update(choices=hall_choices()))

        def do_detail(token, booking_id):
            emp = employee(token)
            if not booking_id:
                raise gr.Error("Enter a booking ID first.")
            try:
                return _detail_md(
                    calendar.booking_detail(emp, int(booking_id)))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_reschedule(token, booking_id, new_date, start_time,
                           end_time, reason):
            emp = employee(token)
            if not booking_id:
                raise gr.Error("Enter a booking ID first.")
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
                raise gr.Error(
                    "Date must be YYYY-MM-DD and times HH:MM (24h).")
            gr.Info(f"Booking {booking.number} rescheduled.")
            return (f"Rescheduled **{booking.number}** to "
                    f"{booking.start_at} → {booking.end_at}.")

        cal_btn.click(do_show,
                      inputs=[session_token, cal_date, cal_range, cal_hall],
                      outputs=[events_md, cal_hall])
        det_btn.click(do_detail, inputs=[session_token, det_id],
                      outputs=[detail_md])
        rs_btn.click(do_reschedule,
                     inputs=[session_token, rs_id, rs_date, rs_start, rs_end,
                             rs_reason],
                     outputs=[rs_msg])

    return {}
