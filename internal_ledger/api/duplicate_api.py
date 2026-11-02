from typing import Literal

import frappe

from internal_ledger.accounting.batch_workflow import process_transactions
from internal_ledger.api.review_api import _transaction_values
from internal_ledger.importing.duplicate_review import resolve
from internal_ledger.permissions import require_ledger_admin


@frappe.whitelist(methods=["GET", "POST"])
def list_duplicates() -> list[dict]:
    items = []
    for row in frappe.get_list("Imported Transaction",
                               filters={"duplicate_of": ["is", "set"],
                                        "duplicate_review_status": ["in", ["Pending", "Confirmed", "Released"]]},
                               fields=["name"], order_by="creation desc", limit_page_length=0):
        candidate = frappe.get_doc("Imported Transaction", row.name)
        candidate.check_permission("read")
        original = frappe.get_doc("Imported Transaction", candidate.duplicate_of)
        original.check_permission("read")
        items.append({"candidate": _transaction_values(candidate), "original": _transaction_values(original)})
    return items


@frappe.whitelist(methods=["POST"])
def resolve_duplicate(name: str, decision: Literal["confirm", "release"], note: str) -> dict:
    require_ledger_admin()
    tx = frappe.get_doc("Imported Transaction", name)
    tx.check_permission("read")
    tx.check_permission("write")
    tx = resolve(name, decision, frappe.session.user, note)
    if decision == "release":
        processing = process_transactions([tx.name])[0]
    else:
        processing = {"name": tx.name, "status": "duplicate", "draft": None,
                      "message": "疑似或已确认重复，未进入分类制证"}
    return {"transaction": tx.as_dict(), "processing": processing}
