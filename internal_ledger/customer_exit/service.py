import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import frappe
from frappe.utils import now_datetime

from internal_ledger.customer_exit.archive import AttachmentReadError, build_customer_exit_archive
from internal_ledger.permissions import (
    _AUDIT_INSERT,
    _CUSTOMER_EXIT_FILE_DELETE,
    _CUSTOMER_EXIT_FILE_WRITE,
    _CUSTOMER_EXIT_WRITE,
    require_enterprise_owner,
)


def _insert_request(actor: str):
    actor = require_enterprise_owner(actor)
    doc = frappe.get_doc({
        "doctype": "Customer Exit Export",
        "status": "Generating",
        "requested_by": actor,
        "requested_at": now_datetime(),
    })
    doc.flags.customer_exit_write = _CUSTOMER_EXIT_WRITE
    doc.insert(ignore_permissions=True)
    doc.flags.customer_exit_write = None
    return doc


def _save_request(doc):
    doc.flags.customer_exit_write = _CUSTOMER_EXIT_WRITE
    try:
        return doc.save(ignore_permissions=True)
    finally:
        doc.flags.customer_exit_write = None


def _record_audit(action: str, request, result: str):
    require_enterprise_owner()
    event = frappe.get_doc({
        "doctype": "Internal Ledger Audit Event",
        "action": action,
        "reference_doctype": request.doctype,
        "reference_name": request.name,
        "result": result,
        "actor": frappe.session.user,
        "timestamp": now_datetime(),
        "company": frappe.db.get_single_value("Internal Ledger Settings", "company"),
    })
    event.flags.audit_insert = _AUDIT_INSERT
    event.insert(ignore_permissions=True)


def serialize_request(doc):
    return {
        "name": doc.name,
        "status": doc.status,
        "requested_by": doc.requested_by,
        "requested_at": str(doc.requested_at or ""),
        "completed_at": str(doc.completed_at or ""),
        "downloaded_at": str(doc.downloaded_at or ""),
        "file": doc.file or "",
        "row_count": int(doc.row_count or 0),
        "attachment_count": int(doc.attachment_count or 0),
        "package_size": int(doc.package_size or 0),
        "package_sha256": doc.package_sha256 or "",
        "failure_code": doc.failure_code or "",
    }


def request_customer_exit_export():
    actor = require_enterprise_owner()
    request = _insert_request(actor)
    _record_audit("request_customer_exit_export", request, "success")
    frappe.enqueue(
        "internal_ledger.customer_exit.service.run_customer_exit_export",
        queue="long",
        enqueue_after_commit=not frappe.flags.in_test,
        now=bool(frappe.flags.in_test),
        name=request.name,
        job_id=f"customer-exit-{request.name}",
    )
    return serialize_request(frappe.get_doc(request.doctype, request.name))


def list_customer_exit_exports():
    require_enterprise_owner()
    fields = [
        "name", "status", "requested_by", "requested_at", "completed_at", "downloaded_at",
        "file", "row_count", "attachment_count", "package_size", "package_sha256", "failure_code",
    ]
    return [serialize_request(row) for row in frappe.get_all(
        "Customer Exit Export", fields=fields, order_by="requested_at desc, name desc",
    )]


def _insert_package_file(request, package: bytes):
    document = frappe.get_doc({
        "doctype": "File",
        "file_name": f"企业数据退出包-{request.name}.zip",
        "content": package,
        "is_private": 1,
        "attached_to_doctype": request.doctype,
        "attached_to_name": request.name,
    })
    document.flags.customer_exit_file_write = _CUSTOMER_EXIT_FILE_WRITE
    document.insert(ignore_permissions=True)
    document.flags.customer_exit_file_write = None
    return document


def _delete_package_file(document):
    document.flags.customer_exit_file_delete = _CUSTOMER_EXIT_FILE_DELETE
    document.delete(ignore_permissions=True)


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, AttachmentReadError):
        return "ATTACHMENT_READ_FAILED"
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "ARCHIVE_VERIFY_FAILED"
    return "DATA_EXPORT_FAILED"


def run_customer_exit_export(name: str) -> None:
    request = frappe.get_doc("Customer Exit Export", name)
    previous_user = frappe.session.user
    temp_dir = None
    package_file = None
    try:
        frappe.set_user(request.requested_by)
        require_enterprise_owner()
        root = Path(frappe.get_site_path("private", "customer-exit-tmp")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="export-", dir=root)).resolve()
        if not temp_dir.is_relative_to(root):
            raise ValueError("temporary export path escaped root")
        target = temp_dir / "customer-exit.zip"
        manifest = build_customer_exit_archive(request.name, target)
        package = target.read_bytes()
        package_file = _insert_package_file(request, package)
        request.status = "Ready"
        request.completed_at = now_datetime()
        request.file = package_file.file_url
        request.row_count = int(manifest["row_count"])
        request.attachment_count = int(manifest["attachment_count"])
        request.package_size = len(package)
        request.package_sha256 = hashlib.sha256(package).hexdigest()
        request.manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        request.failure_code = ""
        _save_request(request)
        _record_audit("complete_customer_exit_export", request, "success")
    except Exception as exc:
        code = _failure_code(exc)
        if package_file and frappe.db.exists("File", package_file.name):
            _delete_package_file(package_file)
        request.reload()
        request.status = "Failed"
        request.file = ""
        request.failure_code = code
        request.completed_at = now_datetime()
        _save_request(request)
        _record_audit("fail_customer_exit_export", request, code)
        frappe.log_error(title=f"Customer exit export {request.name}: {code}", message=frappe.get_traceback())
    finally:
        if temp_dir:
            root = Path(frappe.get_site_path("private", "customer-exit-tmp")).resolve()
            if temp_dir.is_relative_to(root):
                shutil.rmtree(temp_dir, ignore_errors=True)
        frappe.set_user(previous_user)


def download_customer_exit_export(name: str):
    require_enterprise_owner()
    request = frappe.get_doc("Customer Exit Export", name)
    if request.status != "Ready" or not request.file:
        frappe.throw("当前企业数据导出不可下载")
    file_name = frappe.db.get_value("File", {
        "attached_to_doctype": request.doctype,
        "attached_to_name": request.name,
        "file_url": request.file,
    }, "name")
    if not file_name:
        frappe.throw("企业数据导出文件不存在")
    _record_audit("download_customer_exit_export", request, "success")
    request.downloaded_at = now_datetime()
    _save_request(request)
    return frappe.handler.download_file(request.file)


def delete_customer_exit_export(name: str):
    require_enterprise_owner()
    request = frappe.get_doc("Customer Exit Export", name)
    if request.status == "Deleted":
        return serialize_request(request)
    file_name = frappe.db.get_value("File", {
        "attached_to_doctype": request.doctype,
        "attached_to_name": request.name,
    }, "name")
    if file_name:
        _delete_package_file(frappe.get_doc("File", file_name))
    request.status = "Deleted"
    request.file = ""
    _save_request(request)
    _record_audit("delete_customer_exit_export", request, "success")
    return serialize_request(request)
