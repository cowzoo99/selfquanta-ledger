from dataclasses import asdict
from decimal import Decimal
import re
from uuid import uuid4

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
        for field in ("source_id", "dedupe_key", "candidate_dedupe_key"):
            values.pop(field, None)
    return values


def _latest_classification_run():
    from internal_ledger.accounting.batch_workflow import PROCESSING_STATUSES

    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company:
        return None, set()
    events = frappe.get_all("Internal Ledger Audit Event", filters={"company": company,
        "action": "auto_classification_sweep"}, fields=["result"], order_by="creation desc, name desc", limit=1)
    if not events:
        return None, set()
    parts = events[0].result.split(";")
    values = dict(part.split(":", 1) for part in parts if ":" in part)
    summary = {key: int(values.get(key, 0)) for key in PROCESSING_STATUSES}
    run_id = parts[0] if ":" not in parts[0] else ""
    failed = set(frappe.get_all("Internal Ledger Audit Event", filters={"company": company,
        "action": "auto_classification_failure", "result": run_id}, pluck="reference_name")) if run_id else set()
    return summary, failed


@frappe.whitelist(methods=["GET", "POST"])
def get_latest_classification_run():
    frappe.has_permission("Imported Transaction", ptype="read", throw=True)
    summary, _ = _latest_classification_run()
    return {"summary": summary}


@frappe.whitelist(methods=["GET", "POST"])
def get_review_item(name: str):
    doc = _draft(name)
    tx = frappe.get_doc("Imported Transaction", doc.imported_transaction)
    tx.check_permission("read")
    error = None
    # Posted lines are immutable; the save-only state guard does not apply to reads.
    if doc.status != "Posted":
        try:
            doc.validate()
        except frappe.ValidationError as exc:
            error = str(exc)
    values = doc.as_dict()
    for line in values["lines"]:
        account = frappe.get_doc("Account", line["account"])
        account.check_permission("read")
        line.update(account_number=account.account_number, account_name=account.account_name)
    display_title = frappe.db.get_value(
        "Ledger Evidence", {"imported_transaction": tx.name}, "display_title",
    ) or ""
    posting = frappe.db.get_value(
        "Posting Link", {"voucher_draft": doc.name},
        ["name", "journal_entry", "status", "reversal_journal_entry", "reversal_reason"], as_dict=True,
    )
    return {"draft": values, "transaction": _transaction_values(tx), "error": error,
            "can_approve": doc.status == "Draft" and error is None,
            "display_title": display_title, "posting": posting}


@frappe.whitelist(methods=["GET", "POST"])
def list_review_items():
    _, failed = _latest_classification_run()
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    batches = frappe.get_all("Import Batch", filters={"company": company}, pluck="name") if company else []
    if not batches:
        return []
    scoped = frappe.get_all("Imported Transaction", filters={"import_batch": ["in", batches]}, pluck="name")
    items = [get_review_item(row.name) for row in frappe.get_list(
        "Voucher Draft", filters={"company": company, "imported_transaction": ["in", scoped]},
        fields=["name"], order_by="creation desc", limit_page_length=0)] if scoped else []
    drafted = {item["transaction"]["name"] for item in items}
    for tx in frappe.get_list("Imported Transaction", filters={"status": ["in", ["Imported", "Duplicate"]],
                              "import_batch": ["in", batches]},
                              fields=["*"], order_by="occurred_at desc", limit_page_length=0):
        if tx.name in drafted:
            continue
        if tx.status == "Duplicate" or tx.duplicate_review_status in {"Pending", "Confirmed"}:
            items.append({"draft": None, "transaction": _transaction_values(tx), "classification": None,
                          "error": "重复流水只读，请前往重复审核", "can_approve": False})
            continue
        try:
            result = asdict(classify(tx.name))
            error = None if result["status"] == "matched" else result["reason"]
        except frappe.ValidationError as exc:
            result, error = None, str(exc)
        if tx.name in failed:
            error = "生成失败，请检查流水权限、科目及会计期间后重试"
        items.append({"draft": None, "transaction": _transaction_values(tx), "classification": result,
                      "error": error, "can_approve": False,
                      "can_classify": result is None or result["status"] in {"unmatched", "conflict"}})
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
    monthly = [row for row in rows if row["transaction"]["status"] == "Imported"
               and row["transaction"].get("duplicate_review_status") not in {"Pending", "Confirmed"}
               and start <= getdate(row["transaction"]["occurred_at"]) < end]
    batches = frappe.get_list("Import Batch", fields=["name"], order_by="creation desc", limit_page_length=1)
    latest_batch = None
    if batches:
        batch_rows = [row for row in rows if row["transaction"]["import_batch"] == batches[0].name]
        eligible = [row for row in batch_rows if row["transaction"]["status"] == "Imported"
                    and row["transaction"].get("duplicate_review_status") not in {"Pending", "Confirmed"}]
        latest_batch = {"batch_name": batches[0].name, "total": len(eligible),
                        "created": sum(bool(row["draft"] and not row["draft"].get("is_manual_override"))
                                       for row in eligible),
                        "duplicate": len(batch_rows) - len(eligible),
                        "manual": sum(row["draft"] is None for row in eligible)}
    return {"latest_batch": latest_batch, "month": month, "income": str(sum((Decimal(str(row["transaction"]["amount"]))
            for row in monthly if row["transaction"]["direction"] == "income"), Decimal("0"))),
            "expense": str(sum((Decimal(str(row["transaction"]["amount"]))
            for row in monthly if row["transaction"]["direction"] == "expense"), Decimal("0"))),
            "pending_classification": sum(row["draft"] is None for row in monthly),
            "pending_review": sum(bool(row["draft"] and row["draft"]["status"] == "Draft") for row in monthly),
            "posted": sum(bool(row["draft"] and row["draft"]["status"] == "Posted") for row in monthly)}


@frappe.whitelist(methods=["POST"])
def auto_classify_pending():
    from internal_ledger.accounting.batch_workflow import reconcile_unprepared_transactions
    return reconcile_unprepared_transactions()


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
def batch_review_drafts(draft_names, decision: str, reason: str = ""):
    _write_permission()
    names = frappe.parse_json(draft_names) if isinstance(draft_names, str) else draft_names
    if not isinstance(names, list) or not names or any(not isinstance(name, str) or not name for name in names):
        frappe.throw("请选择需要批量处理的凭证草稿")
    if decision not in {"approve", "reject"}:
        frappe.throw("批量审核决定无效")
    if decision == "reject" and (not isinstance(reason, str) or not reason.strip()):
        frappe.throw("批量驳回必须填写原因")
    outcomes = []
    for name in names:
        savepoint = "ledger_batch_review_" + uuid4().hex
        frappe.db.savepoint(savepoint)
        try:
            doc = approve_draft(name) if decision == "approve" else reject_draft(name, reason)
            outcomes.append({"name": name, "status": doc.status,
                             "message": "已审核通过" if doc.status == "Approved" else "已驳回"})
        except frappe.QueryDeadlockError:
            raise
        except Exception as exc:
            frappe.db.rollback(save_point=savepoint)
            frappe.db.release_savepoint(savepoint)
            outcomes.append({"name": name, "status": "failed", "message": str(exc)})
        else:
            frappe.db.release_savepoint(savepoint)
    return outcomes


@frappe.whitelist(methods=["POST"])
def reopen_draft(name: str):
    return _transition(name, "Draft")


@frappe.whitelist(methods=["POST"])
def prepare_matched_transactions(tx_names):
    from internal_ledger.accounting.batch_workflow import prepare_matched_transactions as prepare
    return prepare(frappe.parse_json(tx_names) if isinstance(tx_names, str) else tx_names)
