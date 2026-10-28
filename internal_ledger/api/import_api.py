import base64
import binascii
from dataclasses import asdict

import frappe

from internal_ledger.importing.models import save_batch
from internal_ledger.importing.registry import MAX_FILE_BYTES, parse_statement


def _parse_upload(source, content_base64, payment_account):
    for doctype in ("Import Batch", "Imported Transaction"):
        frappe.has_permission(doctype, ptype="create", throw=True)
    if not isinstance(content_base64, str) or len(content_base64) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise frappe.ValidationError("文件大小超过 2 MiB")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise frappe.ValidationError("文件 Base64 编码无效") from exc
    return parse_statement(source, content, payment_account)


@frappe.whitelist(methods=["POST"])
def preview_import(source: str, content_base64: str, payment_account: str):
    result = _parse_upload(source, content_base64, payment_account)
    rows = []
    for row in result.rows:
        values = asdict(row)
        values.update(amount=str(row.amount), occurred_at=row.occurred_at.isoformat())
        rows.append(values)
    return {"rows": rows, "errors": result.errors, "file_hash": result.file_hash}


@frappe.whitelist(methods=["POST"])
def confirm_import(source: str, content_base64: str, payment_account: str, expected_file_hash: str):
    result = _parse_upload(source, content_base64, payment_account)
    if result.file_hash != expected_file_hash:
        raise frappe.ValidationError("文件哈希与预览不一致，请重新预览")
    if result.errors or not result.rows:
        raise frappe.ValidationError("账单包含错误或没有有效流水，请修正后重新预览")
    return {"batch_name": save_batch(source, result.file_hash, result.rows)}
