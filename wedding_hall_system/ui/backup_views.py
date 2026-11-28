"""Backup & restore screens (Phase 7, presentation tier, SRS §15;
Phase 8: fully bilingual, SRS §16.3).

Thin UI only: parse input, call ONE service method, render the
result. Backup creation, validation and restore are Owner-only
(``backup.manage``); the service refuses corrupt or wrong-version
backups and always makes a safety copy before a restore.
"""
from __future__ import annotations

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.backup_service import BackupService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import LabelRegistry, dir_wrap, lang_for_token, t
from .live_refresh import login_follow_up

logger = get_logger(__name__)


def _list_md(rows: list[dict], lang: str) -> str:
    if not rows:
        return dir_wrap(lang, f"_{t(lang, 'bkp.msg_no_backups')}_")
    out = [f"| {t(lang, 'bkp.table_id')} | {t(lang, 'bkp.table_filename')} | "
           f"{t(lang, 'bkp.table_size')} | {t(lang, 'bkp.table_checksum')} | "
           f"{t(lang, 'bkp.table_created')} | {t(lang, 'bkp.table_validated')} | "
           f"{t(lang, 'bkp.table_on_disk')} |",
           "| --- | --- | ---: | --- | --- | --- | --- |"]
    for r in rows:
        out.append(
            f"| {r['id']} | {r['filename']} | {r['size_bytes']:,} | "
            f"{str(r['checksum'])[:12]}… | {str(r['created_at'])[:19]} | "
            f"{str(r.get('validated_at') or '—')[:19]} | "
            f"{t(lang, 'common.yes' if r.get('exists_on_disk') else 'common.no')} |")
    return dir_wrap(lang, "\n".join(out))


def build_backup_ui(db: DatabaseManager, auth: AuthService,
                    settings: AppSettings, session_token) -> dict:
    """Build the Backup & Restore tab. Returns the LabelRegistry
    result for the language toggle."""
    reg = LabelRegistry()
    backups = BackupService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    with reg.tab("tab.backup"):
        reg.markdown("bkp.heading")
        reg.markdown("bkp.help")
        with gr.Row():
            create_btn = reg.button("bkp.create", variant="primary")
            list_btn = reg.button("bkp.refresh")
        backups_md = gr.Markdown("")

        reg.markdown("bkp.validate_heading")
        with gr.Row():
            bk_id = reg.number("bkp.bk_id", precision=0)
            validate_btn = reg.button("bkp.validate")
            dl_btn = reg.button("bkp.download")
        validate_md = gr.Markdown("")
        dl_file = reg.file("bkp.file")

        reg.markdown("bkp.restore_heading")
        with gr.Row():
            rs_id = reg.number("bkp.rs_id", precision=0)
            rs_confirm = reg.checkbox("bkp.rs_confirm")
            rs_btn = reg.button("bkp.restore_btn", variant="stop")
        restore_md = gr.Markdown("")

        def do_create(token):
            emp = employee(token)
            lang = _lang(token)
            try:
                result = backups.create_backup(emp)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "bkp.msg_created").format(
                filename=result["filename"], hash=result["checksum_sha256"][:12]))
            return _list_md(backups.list_backups(emp), lang)

        def do_list(token):
            emp = employee(token)
            lang = _lang(token)
            try:
                return _list_md(backups.list_backups(emp), lang)
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_validate(token, backup_id):
            emp = employee(token)
            lang = _lang(token)
            if not backup_id:
                raise gr.Error(t(lang, "bkp.msg_need_id"))
            try:
                checks = backups.validate_backup(emp, int(backup_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lines = [t(lang, "bkp.msg_valid").format(id=checks["backup_id"]),
                     f"- {t(lang, 'bkp.msg_file_exists')}: {checks['file_exists']}",
                     f"- {t(lang, 'bkp.msg_checksum')}: {checks['checksum_matches']}",
                     f"- {t(lang, 'bkp.msg_integrity')}: {checks['integrity_check']}",
                     f"- {t(lang, 'bkp.msg_schema')}: v{checks['schema_version']} "
                     f"({t(lang, 'bkp.msg_matches')}: {checks['schema_version_matches']})"]
            gr.Info(t(lang, "bkp.msg_validated").format(id=checks["backup_id"]))
            return dir_wrap(lang, "\n".join(lines))

        def do_download(token, backup_id):
            emp = employee(token)
            lang = _lang(token)
            if not backup_id:
                raise gr.Error(t(lang, "bkp.msg_need_id"))
            try:
                return str(backups.download_backup(emp, int(backup_id)))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_restore(token, backup_id, confirm):
            emp = employee(token)
            lang = _lang(token)
            if not backup_id:
                raise gr.Error(t(lang, "bkp.msg_need_id"))
            try:
                result = backups.restore_backup(
                    emp, int(backup_id), confirm=bool(confirm))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "bkp.msg_restored").format(
                restored=result["restored"], safety=result["safety_copy"]))
            return dir_wrap(lang, t(lang, "bkp.msg_restored_out").format(
                restored=result["restored"], safety=result["safety_copy"],
                id=result["safety_copy_id"]))

        create_btn.click(do_create, inputs=[session_token],
                         outputs=[backups_md])
        list_btn.click(do_list, inputs=[session_token], outputs=[backups_md])
        validate_btn.click(do_validate, inputs=[session_token, bk_id],
                           outputs=[validate_md])
        dl_btn.click(do_download, inputs=[session_token, bk_id],
                     outputs=[dl_file])
        rs_btn.click(do_restore, inputs=[session_token, rs_id, rs_confirm],
                     outputs=[restore_md])

    result = reg.result()
    # A-94: populate the backups list right after sign-in (session/
    # permission-tolerant wrapper, see ui.live_refresh). A non-Owner
    # role has no backup.manage permission, so the list simply stays
    # empty for them instead of popping an error at login.
    result["on_login"] = [
        (login_follow_up(auth, do_list,
                         "do_list_backups_on_login"), [backups_md]),
    ]
    return result
