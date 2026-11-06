from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
import re

import frappe
from frappe.utils import get_last_day, getdate, now_datetime

from internal_ledger.permissions import require_ledger_admin, record_audit


@dataclass(frozen=True)
class CheckResult:
    code: str
    severity: str
    status: str
    count: int
    message: str
    target_route: str


def _result(code, count, message, route, severity="Blocking"):
    count = int(count or 0)
    return CheckResult(code, severity, "Fail" if count else "Pass", count, message, route)


def _period(period: str) -> tuple[date, date]:
    if not isinstance(period, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        frappe.throw("月份格式必须为 YYYY-MM")
    start = getdate(period + "-01")
    return start, get_last_day(start)


def _unposted_drafts(start, end, company):
    filters = {"company": company, "posting_date": ["between", [start, end]],
               "status": ["!=", "Posted"]}
    names = frappe.get_all("Voucher Draft", filters=filters, pluck="name", order_by="creation, name")
    route = f"/app/voucher-draft/{names[0]}" if len(names) == 1 else "/app/voucher-draft?status=Draft"
    return _result("unposted_drafts", len(names), "存在尚未正式过账的凭证草稿", route)


def _pending_duplicates(start, end, company):
    count = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabImported Transaction` tx
        INNER JOIN `tabImport Batch` batch ON batch.name = tx.import_batch
        WHERE batch.company = %s AND DATE(tx.occurred_at) BETWEEN %s AND %s
          AND tx.duplicate_review_status = 'Pending'
    """, (company, start, end))[0][0]
    return _result("pending_duplicates", count, "存在尚未确认的疑似重复流水",
                   "/app/ledger-workbench?step=duplicate-review")


def _unreconciled_bank(start, end, company):
    if not frappe.db.table_exists("Bank Transaction"):
        count = 0
    else:
        count = frappe.db.count("Bank Transaction", {"company": company, "date": ["between", [start, end]],
                                                       "unallocated_amount": [">", 0], "docstatus": ["<", 2]})
    return _result("unreconciled_bank", count, "存在尚未完成对账的银行流水", "/app/bank-reconciliation-tool")


def _negative_stock(start, end, company):
    count = frappe.db.count("Bin", {"company": company, "actual_qty": ["<", 0]}) \
        if frappe.db.table_exists("Bin") else 0
    return _result("negative_stock", count, "存在负库存，需要先核对库存单据", "/app/stock-balance")


def _unsubmitted_invoices(start, end, company):
    count = sum(frappe.db.count(doctype, {"company": company, "posting_date": ["between", [start, end]],
                                         "docstatus": 0})
                for doctype in ("Sales Invoice", "Purchase Invoice") if frappe.db.table_exists(doctype))
    return _result("unsubmitted_invoices", count, "存在尚未提交的销售或采购发票", "/app/accounting")


def _unbalanced_accounts(start, end, company):
    difference = frappe.db.sql("""
        SELECT COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0)
        FROM `tabGL Entry`
        WHERE company = %s AND posting_date BETWEEN %s AND %s AND is_cancelled = 0
    """, (company, start, end))[0][0]
    count = int(abs(Decimal(str(difference or 0))) > Decimal("0.005"))
    return _result("unbalanced_accounts", count, "总账借贷不平衡", "/app/query-report/Trial Balance")


def _missing_payroll(start, end, company):
    employees = frappe.db.count("Employee", {"company": company, "status": "Active"}) \
        if frappe.db.table_exists("Employee") else 0
    slips = frappe.db.count("Salary Slip", {"company": company, "start_date": ["<=", end],
                                            "end_date": [">=", start], "docstatus": 1}) \
        if frappe.db.table_exists("Salary Slip") else 0
    count = employees if employees and not slips else 0
    return _result("missing_payroll", count, "有在职员工但本期没有已提交工资单，请确认是否适用",
                   "/app/salary-slip", "Warning")


def _missing_tax_documents(start, end, company):
    count = 0
    for doctype in ("Sales Invoice", "Purchase Invoice"):
        if frappe.db.table_exists(doctype):
            count += frappe.db.count(doctype, {"company": company,
                "posting_date": ["between", [start, end]], "docstatus": 1,
                "base_total_taxes_and_charges": [">", 0], "taxes_and_charges": ["is", "not set"]})
    return _result("missing_tax_documents", count, "含税发票缺少税费模板，请人工复核",
                   "/app/accounting", "Warning")


CHECKS = {
    "unposted_drafts": _unposted_drafts,
    "pending_duplicates": _pending_duplicates,
    "unreconciled_bank": _unreconciled_bank,
    "negative_stock": _negative_stock,
    "unsubmitted_invoices": _unsubmitted_invoices,
    "unbalanced_accounts": _unbalanced_accounts,
    "missing_payroll": _missing_payroll,
    "missing_tax_documents": _missing_tax_documents,
}


def _aggregate(doctype, filters):
    if not frappe.db.table_exists(doctype):
        return {"count": 0, "modified": ""}
    latest = frappe.get_all(doctype, filters=filters, fields=["modified"],
                            order_by="modified desc", limit=1)
    return {"count": frappe.db.count(doctype, filters),
            "modified": str(latest[0].modified) if latest else ""}


def _source_state(start, end, company):
    imported = frappe.db.sql("""
        SELECT COUNT(*) AS count, MAX(tx.modified) AS modified
        FROM `tabImported Transaction` tx
        INNER JOIN `tabImport Batch` batch ON batch.name = tx.import_batch
        WHERE batch.company = %s AND DATE(tx.occurred_at) BETWEEN %s AND %s
    """, (company, start, end), as_dict=True)[0]
    state = {"Imported Transaction": {"count": int(imported.count or 0),
                                       "modified": str(imported.modified or "")}}
    specifications = {
        "Voucher Draft": {"company": company, "posting_date": ["between", [start, end]]},
        "Bank Transaction": {"company": company, "date": ["between", [start, end]]},
        "Bin": {"company": company},
        "Sales Invoice": {"company": company, "posting_date": ["between", [start, end]]},
        "Purchase Invoice": {"company": company, "posting_date": ["between", [start, end]]},
        "GL Entry": {"company": company, "posting_date": ["between", [start, end]]},
        "Employee": {"company": company},
        "Salary Slip": {"company": company, "start_date": ["<=", end], "end_date": [">=", start]},
    }
    state.update({doctype: _aggregate(doctype, filters) for doctype, filters in specifications.items()})
    return state


def _serialize(doc):
    return {"name": doc.name, "company": doc.company, "period_start": str(doc.period_start),
            "period_end": str(doc.period_end), "status": doc.status, "executed_by": doc.executed_by,
            "executed_at": str(doc.executed_at), "source_hash": doc.source_hash,
            "accounting_period": doc.accounting_period or "",
            "checks": [{"code": row.code, "severity": row.severity, "status": row.status,
                        "count": row.count, "message": row.message, "target_route": row.target_route}
                       for row in doc.checks]}


def get_month_end_run(name: str) -> dict:
    doc = frappe.get_doc("Month End Run", name)
    doc.check_permission("read")
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company or doc.company != company:
        frappe.throw("只能读取当前账套的月结检查", frappe.PermissionError)
    return _serialize(doc)


def run_month_end_checks(company: str, period: str) -> dict:
    require_ledger_admin()
    configured = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not configured or company != configured:
        frappe.throw("只能检查当前账套公司", frappe.PermissionError)
    start, end = _period(period)
    results = [check(start, end, company) for check in CHECKS.values()]
    payload = [{**asdict(row), "count": int(row.count)} for row in results]
    source_hash = hashlib.sha256(json.dumps({"checks": payload, "state": _source_state(start, end, company)},
                                            ensure_ascii=False,
                                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = frappe.db.get_value("Month End Run", {"company": company, "period_start": start,
        "period_end": end, "source_hash": source_hash, "status": ["in", ["Completed", "Closed"]]}, "name")
    if existing:
        return get_month_end_run(existing)
    doc = frappe.get_doc({"doctype": "Month End Run", "company": company,
        "period_start": start, "period_end": end, "status": "Completed",
        "executed_by": frappe.session.user, "executed_at": now_datetime(), "source_hash": source_hash,
        "checks": payload})
    doc.insert(ignore_permissions=True)
    record_audit("run_month_end_checks", doc.doctype, doc.name, "success")
    return _serialize(doc)
