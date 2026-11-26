"""Backup & restore screens (Phase 7, presentation tier, SRS §15).

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

logger = get_logger(__name__)


def _list_md(rows: list[dict]) -> str:
    if not rows:
        return "_No backups yet._"
    out = ["| ID | Filename | Size | Checksum | Created | Validated | On disk |",
           "| --- | --- | ---: | --- | --- | --- | --- |"]
    for r in rows:
        out.append(
            f"| {r['id']} | {r['filename']} | {r['size_bytes']:,} | "
            f"{str(r['checksum'])[:12]}… | {str(r['created_at'])[:19]} | "
            f"{str(r.get('validated_at') or '—')[:19]} | "
            f"{'yes' if r.get('exists_on_disk') else 'NO'} |")
    return "\n".join(out)


def build_backup_ui(db: DatabaseManager, auth: AuthService,
                    settings: AppSettings, session_token) -> dict:
    """Build the Backup & Restore tab."""
    backups = BackupService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    with gr.Tab("Backup & Restore"):
        gr.Markdown("## Backup & Restore — manual backups (SRS §15)")
        gr.Markdown("Owner only. A backup is a SQLite-consistent copy "
                    "(`Connection.backup`) with a checksum + manifest. "
                    "Restore validates the backup first and always keeps a "
                    "safety copy of the live database. Uploaded files are "
                    "inventoried in the manifest, not copied (A-62).")
        with gr.Row():
            create_btn = gr.Button("Create backup now", variant="primary")
            list_btn = gr.Button("Refresh list")
        backups_md = gr.Markdown("")

        gr.Markdown("### Validate / download")
        with gr.Row():
            bk_id = gr.Number(label="Backup ID", precision=0)
            validate_btn = gr.Button("Validate backup")
            dl_btn = gr.Button("Download backup")
        validate_md = gr.Markdown("")
        dl_file = gr.File(label="Backup file")

        gr.Markdown("### Restore (danger: replaces the live database)")
        with gr.Row():
            rs_id = gr.Number(label="Backup ID to restore", precision=0)
            rs_confirm = gr.Checkbox(
                label="I understand: the live database will be replaced "
                      "by this backup (a safety copy is kept).")
            rs_btn = gr.Button("Restore backup", variant="stop")
        restore_md = gr.Markdown("")

        def do_create(token):
            emp = employee(token)
            try:
                result = backups.create_backup(emp)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Backup {result['filename']} created "
                    f"(sha256 {result['checksum_sha256'][:12]}…).")
            return _list_md(backups.list_backups(emp))

        def do_list(token):
            emp = employee(token)
            try:
                return _list_md(backups.list_backups(emp))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_validate(token, backup_id):
            emp = employee(token)
            if not backup_id:
                raise gr.Error("Enter a backup ID first.")
            try:
                checks = backups.validate_backup(emp, int(backup_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lines = [f"**Backup #{checks['backup_id']} — valid.**",
                     f"- File exists: {checks['file_exists']}",
                     f"- Checksum matches: {checks['checksum_matches']}",
                     f"- integrity_check: {checks['integrity_check']}",
                     f"- Schema version: v{checks['schema_version']} "
                     f"(matches: {checks['schema_version_matches']})"]
            gr.Info(f"Backup #{checks['backup_id']} validated.")
            return "\n".join(lines)

        def do_download(token, backup_id):
            emp = employee(token)
            if not backup_id:
                raise gr.Error("Enter a backup ID first.")
            try:
                return str(backups.download_backup(emp, int(backup_id)))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_restore(token, backup_id, confirm):
            emp = employee(token)
            if not backup_id:
                raise gr.Error("Enter a backup ID first.")
            try:
                result = backups.restore_backup(
                    emp, int(backup_id), confirm=bool(confirm))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Restored {result['restored']}; safety copy: "
                    f"{result['safety_copy']}.")
            return (f"**Restored {result['restored']}.**\n\n"
                    f"Safety copy of the previous database: "
                    f"`{result['safety_copy']}` (backup "
                    f"#{result['safety_copy_id']}).")

        create_btn.click(do_create, inputs=[session_token],
                         outputs=[backups_md])
        list_btn.click(do_list, inputs=[session_token], outputs=[backups_md])
        validate_btn.click(do_validate, inputs=[session_token, bk_id],
                           outputs=[validate_md])
        dl_btn.click(do_download, inputs=[session_token, bk_id],
                     outputs=[dl_file])
        rs_btn.click(do_restore, inputs=[session_token, rs_id, rs_confirm],
                     outputs=[restore_md])

    return {}
