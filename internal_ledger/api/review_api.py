from dataclasses import asdict
from decimal import Decimal
import re

import frappe
from frappe.utils import add_months, getdate, nowdate

from internal_ledger.accounting.classifier import classify
from internal_ledger.accounting.drafts import create_draft
from internal_ledger.permissions import require_ledger_admin, record_audit


ALLOWED_TRANSITIONS = {
    "Draft": {"Approved", "Rejected"}, "Rejected": {"Draft"}, "Approved": {"Posted"},
}


def assert_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        frappe.throw("当前状态不可执行该操作")


def _write_permission():
    require_ledger_admin()
    frappe.has_permission("Voucher Draft", ptype="write", throw=True)


def _draft(name, write=False):
    if write:
        _write_permission()
    doc = frappe.get_doc("Voucher Draft", name, for_update=write)
    doc.check_permission("write" if write else "read")
    return doc


def _transaction_values(tx):
    values = dict(tx) if isinstance(tx, dict) else tx.as_dict()
    if frappe.session.user != "Administrator" and "账套管理员" not in frappe.get_roles():
        for field in ("source_id", "dedupe_key"):
            values.pop(field, None)
    return values


@frappe.whitelist(methods=["GET", "POST"])
def get_review_item(name: str):
    doc = _draft(name)
    tx = frappe.get_doc("Imported Transaction", doc.imported_transaction)
    tx.check_permission("read")
    error = None
    try:
        # Runs the same current classification, company and full-line checks as save.
        doc.validate()
    except frappe.ValidationError as exc:
        error = str(exc)
    return {"draft": doc.as_dict(), "transaction": _transaction_values(tx), "error": error,
            "can_approve": doc.status == "Draft" and error is None}


@frappe.whitelist(methods=["GET", "POST"])
def list_review_items():
    items = [get_review_item(row.name) for row in frappe.get_list(
        "Voucher Draft", fields=["name"], order_by="creation desc", limit_page_length=0)]
    drafted = {item["transaction"]["name"] for item in items}
    for tx in frappe.get_list("Imported Transaction", filters={"status": "Imported"},
                              fields=["*"], order_by="occurred_at desc", limit_page_length=0):
        if tx.name in drafted:
            continue
        try:
            result = asdict(classify(tx.name))
            error = None if result["status"] == "matched" else result["reason"]
        except frappe.ValidationError as exc:
            result, error = None, str(exc)
        items.append({"draft": None, "transaction": _transaction_values(tx), "classification": result,
                      "error": error, "can_approve": False})
    return sorted(items, key=lambda item: not bool(item["error"] or (
        item["draft"] and item["draft"]["status"] == "Rejected")))


@frappe.whitelist(methods=["GET", "POST"])
def get_dashboard_counts(month: str | None = None):
    month = month or nowdate()[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        frappe.throw("月份格式必须为 YYYY-MM")
    start = getdate(month + "-01")
    end = add_months(start, 1)
    rows = list_review_items()
    monthly = [row for row in rows if start <= getdate(row["transaction"]["occurred_at"]) < end]
    return {"month": month, "income": str(sum((Decimal(str(row["transaction"]["amount"]))
            for row in monthly if row["transaction"]["direction"] == "income"), Decimal("0"))),
            "expense": str(sum((Decimal(str(row["transaction"]["amount"]))
            for row in monthly if row["transaction"]["direction"] == "expense"), Decimal("0"))),
            "pending_classification": sum(row["draft"] is None for row in monthly),
            "pending_review": sum(bool(row["draft"] and row["draft"]["status"] == "Draft") for row in monthly),
            "posted": sum(bool(row["draft"] and row["draft"]["status"] == "Posted") for row in monthly)}


@frappe.whitelist(methods=["POST"])
def prepare_draft(tx_name: str):
    _write_permission()
    frappe.get_doc("Imported Transaction", tx_name).check_permission("read")
    return frappe.get_doc("Voucher Draft", create_draft(tx_name, classify(tx_name)))


@frappe.whitelist(methods=["POST"])
def update_draft(name: str, lines, summary: str, posting_date: str):
    doc = _draft(name, write=True)
    if doc.status != "Draft":
        frappe.throw("当前状态不可编辑，请先重开驳回草稿")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(posting_date)):
        frappe.throw("记账日期格式必须为 YYYY-MM-DD")
    doc.posting_date = getdate(posting_date)
    rows = frappe.parse_json(lines) if isinstance(lines, str) else lines
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        frappe.throw("分录必须为行列表")
    doc.set("lines", [{key: row.get(key) for key in ("account", "debit", "credit")} for row in rows])
    doc.summary = summary
    doc.is_manual_override = 1
    doc.manual_override_by = frappe.session.user
    doc.flags.review_edit = True
    doc.save()
    record_audit("update_draft", "Voucher Draft", doc.name, "success")
    return doc


def _transition(name, target, reason=None):
    doc = _draft(name, write=True)
    if target == "Approved" and doc.status != "Draft":
        frappe.throw("当前状态不可审核")
    assert_transition(doc.status, target)
    doc.status = target
    doc.rejection_reason = reason if target == "Rejected" else None
    doc.flags.review_transition = target
    doc.save()
    action = {"Approved": "approve_draft", "Rejected": "reject_draft", "Draft": "reopen_draft"}[target]
    record_audit(action, "Voucher Draft", doc.name, "success")
    return doc


@frappe.whitelist(methods=["POST"])
def approve_draft(name: str):
    return _transition(name, "Approved")


@frappe.whitelist(methods=["POST"])
def reject_draft(name: str, reason: str):
    _write_permission()
    if not isinstance(reason, str) or not reason.strip():
        frappe.throw("驳回必须填写原因")
    return _transition(name, "Rejected", reason.strip())


@frappe.whitelist(methods=["POST"])
def reopen_draft(name: str):
    return _transition(name, "Draft")
