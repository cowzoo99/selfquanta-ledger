import frappe

from internal_ledger.accounting.month_end import get_month_end_run as _get_run
from internal_ledger.accounting.month_end import run_month_end_checks as _run_checks
from internal_ledger.permissions import record_audit


def _require_close_manager():
    if frappe.session.user != "Administrator" and not set(frappe.get_roles()) & {
            "账套管理员", "财务主管"}:
        frappe.throw("需要账套管理员或财务主管权限", frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def run_month_end_checks(company: str, period: str):
    return _run_checks(company, period)


@frappe.whitelist(methods=["GET", "POST"])
def get_month_end_run(name: str):
    return _get_run(name)


@frappe.whitelist(methods=["POST"])
def confirm_period_close(run_name: str):
    _require_close_manager()
    run = frappe.get_doc("Month End Run", run_name, for_update=True)
    run.check_permission("write")
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company or run.company != company:
        frappe.throw("只能关闭当前账套期间", frappe.PermissionError)
    blocking = [row for row in run.checks if row.severity == "Blocking" and row.status == "Fail"]
    if blocking:
        frappe.throw("仍有阻断检查未通过，不能关账")
    if run.status == "Closed" and run.accounting_period:
        return {"run": run.name, "status": run.status, "accounting_period": run.accounting_period}
    period_name = f"SelfLedger {company} {str(run.period_start)[:7]}"
    existing = frappe.db.get_value("Accounting Period", {"period_name": period_name}, "name")
    if existing:
        period = frappe.get_doc("Accounting Period", existing)
        if period.company != company or period.disabled:
            frappe.throw("标准会计期间已存在但不可用")
    else:
        period = frappe.get_doc({"doctype": "Accounting Period", "period_name": period_name,
            "company": company, "start_date": run.period_start, "end_date": run.period_end,
            "disabled": 0, "closed_documents": [{"document_type": doctype, "closed": 1}
                for doctype in ("Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry")]})
        period.insert(ignore_permissions=True)
    run.status = "Closed"
    run.accounting_period = period.name
    run.save(ignore_permissions=True)
    record_audit("confirm_period_close", run.doctype, run.name, "success")
    return {"run": run.name, "status": run.status, "accounting_period": period.name}
