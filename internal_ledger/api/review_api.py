from dataclasses import asdict
from decimal import Decimal
import re

import frappe
from frappe.utils import add_months, getdate, nowdate

from internal_ledger.accounting.classifier import classify
from internal_ledger.accounting.drafts import create_draft


ALLOWED_TRANSITIONS = {
    "Draft": {"Approved", "Rejected"}, "Rejected": {"Draft"}, "Approved": {"Posted"},
}


def assert_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        frappe.throw("当前状态不可执行该操作")


def _write_permission():
    # Administrator is Frappe's built-in superuser, including in site tests.
    if frappe.session.user != "Administrator" and "账套管理员" not in frappe.get_roles():
        frappe.throw("仅账套管理员可执行人工审核操作", frappe.PermissionError)
    frappe.has_permission("Voucher Draft", ptype="write", throw=True)


def _draft(name, write=False):
    if write:
        _write_permission()
    doc = frappe.get_doc("Voucher Draft", name, for_update=write)
    doc.check_permission("write" if write else "read")
    return doc


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
    return {"draft": doc.as_dict(), "transaction": tx.as_dict(), "error": error,
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
        items.append({"draft": None, "transaction": tx, "classification": result,
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
    return doc.save()


def _transition(name, target, reason=None):
    doc = _draft(name, write=True)
    if target == "Approved" and doc.status != "Draft":
        frappe.throw("当前状态不可审核")
    assert_transition(doc.status, target)
    doc.status = target
    doc.rejection_reason = reason if target == "Rejected" else None
    doc.flags.review_transition = target
    return doc.save()


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
