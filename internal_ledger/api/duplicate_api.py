from typing import Literal
from uuid import uuid4

import frappe

from internal_ledger.accounting.batch_workflow import process_transactions
from internal_ledger.api.review_api import _transaction_values
from internal_ledger.importing.duplicate_review import resolve
from internal_ledger.permissions import require_ledger_admin, record_audit


def _duplicate_scope(batch, transaction):
    if any(value is not None and not isinstance(value, str) for value in (batch, transaction)):
        frappe.throw("重复复核范围无效")
    if not (batch or transaction):
        return None
    if transaction:
        tx = frappe.get_doc("Imported Transaction", transaction)
        tx.check_permission("read")
        if batch and tx.import_batch != batch:
            frappe.throw("重复复核范围与当前账套或流水不一致", frappe.PermissionError)
        batch = tx.import_batch
    doc = frappe.get_doc("Import Batch", batch)
    doc.check_permission("read")
    if doc.company != frappe.db.get_single_value("Internal Ledger Settings", "company"):
        frappe.throw("重复复核范围与当前账套或流水不一致", frappe.PermissionError)
    return batch


@frappe.whitelist(methods=["GET", "POST"])
def list_duplicates(batch: str | None = None, transaction: str | None = None) -> list[dict]:
    batch = _duplicate_scope(batch, transaction)
    filters = {"duplicate_of": ["is", "set"],
               "duplicate_review_status": ["in", ["Pending", "Confirmed", "Released"]]}
    if batch:
        filters["import_batch"] = batch
    if transaction:
        filters["name"] = transaction
    items = []
    for row in frappe.get_list("Imported Transaction",
                               filters=filters,
                               fields=["name"], order_by="creation desc", limit_page_length=0):
        candidate = frappe.get_doc("Imported Transaction", row.name)
        candidate.check_permission("read")
        original = frappe.get_doc("Imported Transaction", candidate.duplicate_of)
        original.check_permission("read")
        items.append({"candidate": _transaction_values(candidate), "original": _transaction_values(original)})
    return items


@frappe.whitelist(methods=["POST"])
def resolve_duplicate(name: str, decision: Literal["confirm", "release"], note: str,
                      batch: str | None = None, transaction: str | None = None) -> dict:
    require_ledger_admin()
    scope_batch = _duplicate_scope(batch, transaction)
    if transaction and transaction != name:
        frappe.throw("重复复核范围与当前账套或流水不一致", frappe.PermissionError)
    tx = frappe.get_doc("Imported Transaction", name)
    tx.check_permission("read")
    tx.check_permission("write")
    if scope_batch and tx.import_batch != scope_batch:
        frappe.throw("重复复核范围与当前账套或流水不一致", frappe.PermissionError)
    savepoint = "ledger_duplicate_processing_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        tx = resolve(name, decision, frappe.session.user, note)
        if decision == "release":
            processing = process_transactions([tx.name])[0]
            record_audit("duplicate_release_processing", "Imported Transaction", tx.name, processing["status"])
        else:
            processing = {"name": tx.name, "status": "duplicate", "draft": None,
                          "message": "疑似或已确认重复，未进入分类制证"}
    except frappe.QueryDeadlockError:
        raise
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        raise
    frappe.db.release_savepoint(savepoint)
    return {"transaction": tx.as_dict(), "processing": processing}


@frappe.whitelist(methods=["POST"])
def resolve_duplicates(names, decision: Literal["confirm", "release"], note: str,
                       batch: str | None = None, transaction: str | None = None) -> list[dict]:
    require_ledger_admin()
    selected = frappe.parse_json(names) if isinstance(names, str) else names
    if not isinstance(selected, list) or not selected or any(not isinstance(name, str) or not name for name in selected):
        frappe.throw("请选择需要批量复核的重复流水")
    if decision not in {"confirm", "release"}:
        frappe.throw("重复复核决定无效")
    if decision == "release" and (not isinstance(note, str) or not note.strip()):
        frappe.throw("批量保留入账必须填写原因")
    _duplicate_scope(batch, transaction)
    outcomes = []
    for name in selected:
        try:
            result = resolve_duplicate(name, decision, note, batch=batch, transaction=transaction)
            processing = result["processing"]
            outcomes.append({"name": name, "status": processing["status"],
                             "message": processing.get("message", "处理完成"),
                             "draft": processing.get("draft")})
        except frappe.QueryDeadlockError:
            raise
        except Exception as exc:
            outcomes.append({"name": name, "status": "failed", "message": str(exc), "draft": None})
    return outcomes
