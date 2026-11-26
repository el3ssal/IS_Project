"""File management screens (Phase 6, presentation tier, SRS §11).

Thin UI only: parse input, call ONE service method, render the result.
Uploads are validated by the service (extension allowlist, size,
magic bytes); downloads and previews go only through the authorized
service handler — the uploads directory is never served statically
(GAP G-15). Deletion is a permission-controlled soft-delete.
"""
from __future__ import annotations

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic.attachment_service import ATTACHABLE_ENTITIES, AttachmentService
from ..logic.auth_service import AuthService
from ..logic.models import ATTACHMENT_CATEGORIES
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import dir_wrap

logger = get_logger(__name__)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _list_md(rows: list[dict]) -> str:
    return _md_table(
        ["ID", "Filename", "Category", "Entity", "Record", "Size (bytes)",
         "Uploaded by", "Uploaded"],
        [[r["id"], r["original_filename"], r["category"], r["entity_type"],
          r["entity_id"], r["size_bytes"], r["uploaded_by"] or "—",
          str(r.get("uploaded_at", ""))[:19]] for r in rows])


def build_files_ui(db: DatabaseManager, auth: AuthService,
                   settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Files tab. Returns label targets for the toggle."""
    files = AttachmentService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    with gr.Tab("Files • ملفات"):
        gr.Markdown("## Files — upload, preview, download, delete (SRS §11)")
        gr.Markdown("Allowed: PDF, JPG, PNG (max 10 MB). Files attach to a "
                    "customer, booking, contract, payment or expense.")
        with gr.Row():
            up_entity = gr.Dropdown(list(ATTACHABLE_ENTITIES), value="customer",
                                    label="Attach to")
            up_entity_id = gr.Number(label="Record ID", precision=0)
            up_category = gr.Dropdown(list(ATTACHMENT_CATEGORIES),
                                      value="Other", label="Category")
            up_file = gr.File(label="File (PDF, JPG or PNG, max 10 MB)",
                              type="filepath")
            up_btn = gr.Button("Upload file", variant="primary")
        up_msg = gr.Markdown("")

        gr.Markdown("### List files")
        with gr.Row():
            li_entity = gr.Dropdown([""] + list(ATTACHABLE_ENTITIES),
                                    value="", label="Attach to (optional)")
            li_entity_id = gr.Number(label="Record ID (optional)", precision=0)
            li_category = gr.Dropdown([""] + list(ATTACHMENT_CATEGORIES),
                                      value="", label="Category (optional)")
            li_btn = gr.Button("List files")
        files_list = gr.Markdown("")

        gr.Markdown("### Download / preview / delete")
        with gr.Row():
            att_id = gr.Number(label="Attachment ID", precision=0)
            dl_btn = gr.Button("Download")
            prev_btn = gr.Button("Preview")
            del_btn = gr.Button("Delete file")
        att_file = gr.File(label="Downloaded attachment")
        att_image = gr.Image(label="Image preview")

        def do_upload(token, entity_type, entity_id, category, file_path, lang):
            emp = employee(token)
            if not entity_id:
                raise gr.Error("Enter a record ID first.")
            if not file_path:
                raise gr.Error("Choose a file to upload.")
            try:
                result = files.upload(
                    emp, entity_type=entity_type, entity_id=int(entity_id),
                    src_path=str(file_path),
                    original_filename=str(file_path).rsplit("/", 1)[-1],
                    category=category or "Other")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"File stored as attachment #{result['attachment_id']} "
                    f"(sha256 {result['sha256'][:12]}…).")
            return dir_wrap(lang or "ar", _list_md(
                files.list_attachments(emp, entity_type=entity_type,
                                       entity_id=int(entity_id))))

        def do_list(token, entity_type, entity_id, category, lang):
            emp = employee(token)
            try:
                rows = files.list_attachments(
                    emp,
                    entity_type=entity_type or None,
                    entity_id=int(entity_id) if entity_id else None,
                    category=category or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return dir_wrap(lang or "ar", _list_md(rows))

        def do_download(token, attachment_id):
            emp = employee(token)
            if not attachment_id:
                raise gr.Error("Enter an attachment ID first.")
            try:
                return str(files.download(emp, int(attachment_id)))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_preview(token, attachment_id):
            emp = employee(token)
            if not attachment_id:
                raise gr.Error("Enter an attachment ID first.")
            try:
                path, mime = files.preview(emp, int(attachment_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            if mime.startswith("image/"):
                return str(path), None
            gr.Info(f"{mime} — use Download for this file type.")
            return None, str(path)

        def do_delete(token, attachment_id, lang):
            emp = employee(token)
            if not attachment_id:
                raise gr.Error("Enter an attachment ID first.")
            try:
                files.delete(emp, int(attachment_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Attachment #{int(attachment_id)} deleted.")
            return dir_wrap(lang or "ar", _list_md(
                files.list_attachments(emp)))

        up_btn.click(do_upload,
                     inputs=[session_token, up_entity, up_entity_id,
                             up_category, up_file, lang_state],
                     outputs=[up_msg])
        li_btn.click(do_list,
                     inputs=[session_token, li_entity, li_entity_id,
                             li_category, lang_state],
                     outputs=[files_list])
        dl_btn.click(do_download, inputs=[session_token, att_id],
                     outputs=[att_file])
        prev_btn.click(do_preview, inputs=[session_token, att_id],
                       outputs=[att_image, att_file])
        del_btn.click(do_delete,
                      inputs=[session_token, att_id, lang_state],
                      outputs=[files_list])

    return {
        "labels": [(up_entity, "files.entity_type"),
                   (up_entity_id, "files.entity_id"),
                   (up_category, "files.category"),
                   (up_btn, "files.upload"),
                   (li_entity, "files.entity_type"),
                   (li_entity_id, "files.entity_id"),
                   (li_category, "files.category"),
                   (li_btn, "files.list"),
                   (att_id, "files.attachment_id"),
                   (dl_btn, "files.download"),
                   (prev_btn, "files.preview"),
                   (del_btn, "files.delete")],
        "renders": [],
    }
