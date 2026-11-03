import base64
import binascii
from contextlib import ExitStack, contextmanager
from pathlib import Path, PureWindowsPath
from uuid import uuid4

import frappe
from frappe.core.doctype.file.utils import get_safe_file_name

from internal_ledger.permissions import (
    _EVIDENCE_FILE_WRITE, _EVIDENCE_WRITE, record_audit, require_ledger_admin,
)


MAX_EVIDENCE_FILE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_FILES = 10
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = b"PK\x03\x04"
_FORMATS = {
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".webp": ("image/webp", b"RIFF"),
    ".pdf": ("application/pdf", b"%PDF-"),
    ".doc": ("application/msword", _OLE),
    ".xls": ("application/vnd.ms-excel", _OLE),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", _ZIP),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _ZIP),
    ".txt": ("text/plain", b""),
    ".csv": ("text/csv", b""),
}
ALLOWED_EXTENSIONS = set(_FORMATS)


def _decode_upload(filename, content_base64, mime_type):
    safe_name = get_safe_file_name(PureWindowsPath(filename.strip()).name)
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS or not safe_name.isprintable():
        frappe.throw("不支持的附件文件名或扩展名")
    mime, signature = _FORMATS[extension]
    if mime_type.strip().lower() != mime:
        frappe.throw("附件 MIME 类型与扩展名不符")
    if len(content_base64) > 4 * ((MAX_EVIDENCE_FILE_BYTES + 2) // 3):
        frappe.throw("附件大小不能超过 10 MiB")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error):
        frappe.throw("附件不是有效的 Base64")
    if len(content) > MAX_EVIDENCE_FILE_BYTES:
        frappe.throw("附件大小不能超过 10 MiB")
    if (not content or not content.startswith(signature)
            or (extension == ".webp" and content[8:12] != b"WEBP")
            or (extension in {".txt", ".csv"} and b"\x00" in content)):
        frappe.throw("文件内容与扩展名不符")
    return safe_name, content


def _transaction(tx_name, write=False):
    if write:
        require_ledger_admin()
    tx = frappe.get_doc("Imported Transaction", tx_name, for_update=write)
    tx.check_permission("write" if write else "read")
    return tx


def _evidence(tx, write=False):
    name = frappe.db.get_value("Ledger Evidence", {"imported_transaction": tx.name}, for_update=write)
    if name:
        evidence = frappe.get_doc("Ledger Evidence", name, for_update=write)
        evidence.check_permission("write" if write else "read")
    else:
        evidence = frappe.get_doc({"doctype": "Ledger Evidence", "imported_transaction": tx.name})
    if write:
        evidence.flags.ledger_evidence_write = _EVIDENCE_WRITE
    return evidence


def _file_filters(evidence):
    return {"attached_to_doctype": "Ledger Evidence", "attached_to_name": evidence.name, "is_private": 1}


def _serialize_evidence(evidence):
    files = []
    if evidence.name:
        files = frappe.db.get_values("File", _file_filters(evidence),
            ["name", "file_name", "file_url", "file_size"], as_dict=True, order_by="creation asc, name asc",
            for_update=evidence.flags.ledger_evidence_write is _EVIDENCE_WRITE)
        for row in files:
            row.extension = Path(row.file_name).suffix.lower()
            row.is_image = row.extension in IMAGE_EXTENSIONS
    return {"name": evidence.name, "transaction": evidence.imported_transaction,
            "note_html": evidence.note_html or "", "attachments": files}


@contextmanager
def _audited_write(tx, action):
    point = "ledger_evidence_" + uuid4().hex
    frappe.db.savepoint(point)
    with ExitStack() as undo_files:
        try:
            # Writing the audit first avoids any file side effect on audit failure.
            record_audit(action, tx.doctype, tx.name, "success")
            yield undo_files
        except frappe.QueryDeadlockError:
            raise  # MariaDB has already rolled back the transaction.
        except Exception:
            frappe.db.rollback(save_point=point)
            frappe.db.release_savepoint(point)
            raise
        else:
            frappe.db.release_savepoint(point)
            undo_files.pop_all()


@frappe.whitelist(methods=["GET", "POST"])
def get_evidence(tx_name: str) -> dict:
    return _serialize_evidence(_evidence(_transaction(tx_name)))


@frappe.whitelist(methods=["POST"])
def save_evidence(tx_name: str, note_html: str) -> dict:
    tx = _transaction(tx_name, write=True)
    evidence = _evidence(tx, write=True)
    with _audited_write(tx, "update_evidence_note"):
        evidence.note_html = note_html
        evidence.save()
        return _serialize_evidence(evidence)


@frappe.whitelist(methods=["POST"])
def upload_evidence(tx_name: str, filename: str, content_base64: str, mime_type: str) -> dict:
    tx = _transaction(tx_name, write=True)
    safe_name, content = _decode_upload(filename, content_base64, mime_type)
    evidence = _evidence(tx, write=True)
    with _audited_write(tx, "upload_evidence") as undo_files:
        if not evidence.name:
            evidence.insert()
        existing = frappe.db.get_values("File", _file_filters(evidence), "name", for_update=True)
        if len(existing) >= MAX_EVIDENCE_FILES:
            frappe.throw("每笔流水最多上传 10 个附件")
        attachment = frappe.get_doc({"doctype": "File", "file_name": safe_name, "content": content,
            "attached_to_doctype": evidence.doctype, "attached_to_name": evidence.name, "is_private": 1})
        attachment.flags.evidence_file_write = _EVIDENCE_FILE_WRITE
        # A SQL savepoint does not trigger File's native full-rollback callback.
        undo_files.callback(attachment.on_rollback)
        attachment.insert()
        if frappe.db.get_value("File", {"file_url": attachment.file_url,
                "attached_to_doctype": "Internal Ledger Source File"}, for_update=True):
            frappe.throw("附件不能与导入原始文件共用下载地址")
        return _serialize_evidence(evidence)


@frappe.whitelist(methods=["POST"])
def delete_evidence_file(tx_name: str, attachment_name: str) -> dict:
    tx = _transaction(tx_name, write=True)
    evidence = _evidence(tx, write=True)
    if not evidence.name or not frappe.db.get_value("File",
            {**_file_filters(evidence), "name": attachment_name}, for_update=True):
        frappe.throw("附件不属于当前流水", frappe.PermissionError)
    attachment = frappe.get_doc("File", attachment_name, for_update=True)
    attachment.flags.evidence_file_write = _EVIDENCE_FILE_WRITE
    with _audited_write(tx, "delete_evidence") as undo_files:
        # Native File.on_trash unlinks immediately, even before link validation.
        path = Path(attachment.get_full_path())
        original = path.read_bytes()

        def restore_blob():
            if not path.exists() and frappe.db.exists("File", attachment.name):
                path.write_bytes(original)

        undo_files.callback(restore_blob)
        frappe.db.after_rollback.add(restore_blob)
        attachment.delete()
        return _serialize_evidence(evidence)
