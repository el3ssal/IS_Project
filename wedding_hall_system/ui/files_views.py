"""File management screens (Phase 6, presentation tier, SRS §11;
Phase 8: fully bilingual, SRS §16.3).

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
from .i18n import LabelRegistry, dir_wrap, lang_for_token, t

logger = get_logger(__name__)

ENTITY_KEYS = {
    "customer": "files.entity_customer",
    "booking": "files.entity_booking",
    "contract": "files.entity_contract",
    "payment": "files.entity_payment",
    "expense": "files.entity_expense",
}
CAT_KEYS = {
    "Identity Documents": "files.cat_identity",
    "Contracts": "files.cat_contracts",
    "Receipts": "files.cat_receipts",
    "Event Photos": "files.cat_photos",
    "Customer Documents": "files.cat_customer_docs",
    "Other": "files.cat_other",
}


def _md_table(headers: list[str], rows: list[list[str]], lang: str) -> str:
    if not rows:
        return f"_{t(lang, 'common.no_records')}_"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _list_md(rows: list[dict], lang: str) -> str:
    return _md_table(
        [t(lang, "files.table_id"), t(lang, "files.table_filename"),
         t(lang, "files.table_category"), t(lang, "files.table_entity"),
         t(lang, "files.table_record"), t(lang, "files.table_size"),
         t(lang, "files.table_uploaded_by"), t(lang, "files.table_uploaded")],
        [[r["id"], r["original_filename"],
          t(lang, CAT_KEYS.get(r["category"], "files.cat_other")),
          t(lang, ENTITY_KEYS.get(r["entity_type"], "files.entity_customer")),
          r["entity_id"], r["size_bytes"], r["uploaded_by"] or "—",
          str(r.get("uploaded_at", ""))[:19]] for r in rows], lang)


def build_files_ui(db: DatabaseManager, auth: AuthService,
                   settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Files tab. Returns label targets for the toggle."""
    reg = LabelRegistry()
    files = AttachmentService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    with reg.tab("tab.files"):
        reg.markdown("files.heading")
        reg.markdown("files.help")
        with gr.Row():
            up_entity = reg.dropdown_t(
                "files.entity_type",
                [(ENTITY_KEYS[e], e) for e in ATTACHABLE_ENTITIES],
                value="customer")
            up_entity_id = reg.number("files.entity_id", precision=0)
            up_category = reg.dropdown_t(
                "files.category",
                [(CAT_KEYS[c], c) for c in ATTACHMENT_CATEGORIES],
                value="Other")
            up_file = reg.file("files.file", type="filepath")
            up_btn = reg.button("files.upload", variant="primary")
        up_msg = gr.Markdown("")

        reg.markdown("files.list_heading")
        with gr.Row():
            li_entity = reg.dropdown_data(
                "files.li_entity",
                lambda lang: [(t(lang, "files.any"), "")] +
                [(t(lang, ENTITY_KEYS[e]), e) for e in ATTACHABLE_ENTITIES])
            li_entity_id = reg.number("files.li_entity_id", precision=0)
            li_category = reg.dropdown_data(
                "files.li_category",
                lambda lang: [(t(lang, "files.any"), "")] +
                [(t(lang, CAT_KEYS[c]), c) for c in ATTACHMENT_CATEGORIES])
            li_btn = reg.button("files.list")
        files_list = gr.Markdown("")

        reg.markdown("files.download_heading")
        with gr.Row():
            att_id = reg.number("files.attachment_id", precision=0)
            dl_btn = reg.button("files.download")
            prev_btn = reg.button("files.preview")
            del_btn = reg.button("files.delete")
        att_file = reg.file("files.downloaded")
        att_image = reg.image("files.preview_image")

        def do_upload(token, entity_type, entity_id, category, file_path, lang):
            emp = employee(token)
            lang = lang or "ar"
            if not entity_id:
                raise gr.Error(t(lang, "files.msg_need_record"))
            if not file_path:
                raise gr.Error(t(lang, "files.msg_need_file"))
            try:
                result = files.upload(
                    emp, entity_type=entity_type, entity_id=int(entity_id),
                    src_path=str(file_path),
                    original_filename=str(file_path).rsplit("/", 1)[-1],
                    category=category or "Other")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "files.msg_stored").format(
                id=result["attachment_id"], hash=result["sha256"][:12]))
            return dir_wrap(lang, _list_md(
                files.list_attachments(emp, entity_type=entity_type,
                                       entity_id=int(entity_id)), lang))

        def do_list(token, entity_type, entity_id, category, lang):
            emp = employee(token)
            lang = lang or "ar"
            try:
                rows = files.list_attachments(
                    emp,
                    entity_type=entity_type or None,
                    entity_id=int(entity_id) if entity_id else None,
                    category=category or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return dir_wrap(lang, _list_md(rows, lang))

        def do_download(token, attachment_id):
            emp = employee(token)
            lang = _lang(token)
            if not attachment_id:
                raise gr.Error(t(lang, "files.msg_need_attachment"))
            try:
                return str(files.download(emp, int(attachment_id)))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_preview(token, attachment_id):
            emp = employee(token)
            lang = _lang(token)
            if not attachment_id:
                raise gr.Error(t(lang, "files.msg_need_attachment"))
            try:
                path, mime = files.preview(emp, int(attachment_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            if mime.startswith("image/"):
                return str(path), None
            gr.Info(t(lang, "files.msg_preview_type").format(mime=mime))
            return None, str(path)

        def do_delete(token, attachment_id, lang):
            emp = employee(token)
            lang = lang or "ar"
            if not attachment_id:
                raise gr.Error(t(lang, "files.msg_need_attachment"))
            try:
                files.delete(emp, int(attachment_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "files.msg_deleted").format(id=int(attachment_id)))
            return dir_wrap(lang, _list_md(
                files.list_attachments(emp), lang))

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

    return reg.result()
