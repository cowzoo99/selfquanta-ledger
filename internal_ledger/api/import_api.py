import base64
import binascii
from dataclasses import asdict
import json

import frappe

from internal_ledger.accounting.batch_workflow import process_transactions
from internal_ledger.importing.models import save_batch
from internal_ledger.importing.registry import MAX_FILE_BYTES, parse_statement
from internal_ledger.permissions import require_ledger_admin, _SOURCE_INSERT, _IMPORT_WRITE


PROCESSING_STATUSES = ("created", "duplicate", "manual", "conflict", "failed")


def summarize_processing(outcomes: list[dict]) -> dict:
    summary = {"total": len(outcomes), **{status: 0 for status in PROCESSING_STATUSES}}
    for row in outcomes:
        if row["status"] in PROCESSING_STATUSES:
            summary[row["status"]] += 1
    return summary


@frappe.whitelist(methods=["GET", "POST"])
def list_payment_accounts():
    require_ledger_admin()
    company = frappe.get_single("Internal Ledger Settings").company
    return frappe.get_all("Account", filters={
        "company": company, "is_group": 0, "disabled": 0,
        "account_number": ["in", ["1001", "100201", "100202", "100203"]],
    }, fields=["name", "account_name", "account_number"], order_by="account_number")


def _parse_upload(source, content_base64, payment_account):
    require_ledger_admin()
    for doctype in ("Import Batch", "Imported Transaction"):
        frappe.has_permission(doctype, ptype="create", throw=True)
    if not isinstance(content_base64, str) or len(content_base64) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise frappe.ValidationError("文件大小超过 10 MiB")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise frappe.ValidationError("文件 Base64 编码无效") from exc
    company = frappe.get_single("Internal Ledger Settings").company
    return parse_statement(source, content, payment_account, company)


@frappe.whitelist(methods=["POST"])
def preview_import(source: str, content_base64: str, payment_account: str, filename: str | None = None):
    result = _parse_upload(source, content_base64, payment_account)
    rows = []
    for row in result.rows:
        values = asdict(row)
        values.update(amount=str(row.amount), occurred_at=row.occurred_at.isoformat())
        rows.append(values)
    return {"rows": rows, "errors": result.errors, "file_hash": result.file_hash,
            "detected_source": result.source or source}


@frappe.whitelist(methods=["POST"])
def confirm_import(source: str, content_base64: str, payment_account: str, expected_file_hash: str,
                   filename: str | None = None, corrections_json: str | None = None):
    result = _parse_upload(source, content_base64, payment_account)
    if result.file_hash != expected_file_hash:
        raise frappe.ValidationError("文件哈希与预览不一致，请重新预览")
    if result.errors or not result.rows:
        raise frappe.ValidationError("账单包含错误或没有有效流水，请修正后重新预览")
    if corrections_json:
        from internal_ledger.importing.corrections import apply_corrections
        try:
            result = result.__class__(
                apply_corrections(result.rows, json.loads(corrections_json)), result.errors,
                result.file_hash, result.source,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            raise frappe.ValidationError(str(exc)) from exc
    actual_source = result.source or source
    name = save_batch(actual_source, result.file_hash, result.rows)
    batch = frappe.get_doc("Import Batch", name)
    batch.check_permission("write")
    carrier = frappe.get_doc({"doctype": "Internal Ledger Source File", "import_batch": name})
    carrier.flags.source_insert = _SOURCE_INSERT
    carrier.insert()
    suffix = ".csv"
    if isinstance(filename, str):
        candidate = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if candidate in {"csv", "pdf", "png", "jpg", "jpeg"}:
            suffix = "." + candidate
    attachment = frappe.get_doc({"doctype": "File", "file_name": f"{actual_source}-{name}{suffix}",
        "content": base64.b64decode(content_base64), "attached_to_doctype": carrier.doctype,
        "attached_to_name": carrier.name, "is_private": 1})
    attachment.flags.source_insert = _SOURCE_INSERT
    attachment.insert()
    carrier.raw_file = attachment.file_url
    carrier.save()
    batch.flags.source_insert = _SOURCE_INSERT
    batch.flags.ledger_import_write = _IMPORT_WRITE
    batch.raw_file = carrier.name
    batch.save()
    tx_names = frappe.get_all("Imported Transaction", filters={"import_batch": name},
                              pluck="name", order_by="raw_row")
    processing = process_transactions(tx_names)
    return {"batch_name": name, "detected_source": actual_source,
            "processing": processing, "summary": summarize_processing(processing)}
