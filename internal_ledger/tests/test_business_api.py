import unittest
from uuid import uuid4

import frappe

from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_review_api import set_up, tear_down


CREATED = []


def raw_order(doctype, company, date, total):
    name = "BUSINESS-" + uuid4().hex[:12]
    values = {"doctype": doctype, "name": name, "company": company, "grand_total": total,
              "status": "To Deliver and Bill" if doctype == "Sales Order" else "To Receive and Bill",
              "docstatus": 1}
    values["transaction_date"] = date
    frappe.get_doc(values).db_insert()
    CREATED.append((doctype, name))
    return name


def raw_metric_doc(doctype, **values):
    name = "BUSINESS-" + uuid4().hex[:12]
    frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()
    CREATED.append((doctype, name))
    return name


def card(result, section, label):
    return next(row for row in result["sections"][section]["cards"] if row["label"] == label)


def test_dashboard_filters_company_and_period_and_reconciles_sales_total():
    from internal_ledger.api.business_api import get_business_dashboard

    company = frappe.flags.classification_company
    raw_order("Sales Order", company, "2026-10-10", 120)
    raw_order("Sales Order", company, "2026-09-30", 999)
    raw_order("Sales Order", "Other Company", "2026-10-10", 777)
    result = get_business_dashboard(company, "2026-10-01", "2026-10-31")
    assert card(result, "sales", "销售订单金额")["value"] == 120
    assert card(result, "sales", "销售订单数量")["value"] == 1
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        get_business_dashboard("Other Company", "2026-10-01", "2026-10-31")


def test_dashboard_suppresses_payroll_for_non_hr_non_finance_roles():
    from internal_ledger.api.business_api import ALLOWED_ROUTES, get_business_dashboard

    company = frappe.flags.classification_company
    frappe.set_user(user("销售"))
    result = get_business_dashboard(company, "2026-10-01", "2026-10-31")
    assert result["sections"]["hr"]["restricted"] is False
    assert not any(row["label"] == "工资金额" for row in result["sections"]["hr"]["cards"])
    routes = {row["target_route"] for section in result["sections"].values() for row in section["cards"]}
    assert routes <= ALLOWED_ROUTES
    frappe.set_user("Administrator")


def test_hr_dashboard_counts_only_current_company_period_payroll_drafts_and_submitted_salary():
    from internal_ledger.api.business_api import get_business_dashboard

    company = frappe.flags.classification_company
    raw_metric_doc("Payroll Entry", company=company, start_date="2026-10-01", end_date="2026-10-31",
                   posting_date="2026-10-31", docstatus=0)
    raw_metric_doc("Payroll Entry", company=company, start_date="2026-09-01", end_date="2026-09-30",
                   posting_date="2026-09-30", docstatus=0)
    raw_metric_doc("Payroll Entry", company="Other Company", start_date="2026-10-01",
                   end_date="2026-10-31", posting_date="2026-10-31", docstatus=0)
    raw_metric_doc("Salary Slip", company=company, employee="TEST-EMPLOYEE", employee_name="测试员工",
                   posting_date="2026-10-31", start_date="2026-10-01", end_date="2026-10-31",
                   gross_pay=5000, net_pay=5000, docstatus=1)
    raw_metric_doc("Salary Slip", company="Other Company", employee="OTHER-EMPLOYEE",
                   employee_name="其他员工", posting_date="2026-10-31", start_date="2026-10-01",
                   end_date="2026-10-31", gross_pay=9000, net_pay=9000, docstatus=1)

    result = get_business_dashboard(company, "2026-10-01", "2026-10-31")
    assert card(result, "hr", "工资草稿")["value"] == 1
    assert card(result, "hr", "工资草稿")["target_route"] == "/app/payroll-entry"
    assert card(result, "hr", "工资金额")["value"] == 5000
    assert card(result, "hr", "工资金额")["target_route"] == "/app/salary-slip"


def test_one_failed_section_returns_a_partial_dashboard():
    import internal_ledger.api.business_api as business_api

    company = frappe.flags.classification_company
    original = business_api.SECTION_LOADERS["sales"]
    business_api.SECTION_LOADERS["sales"] = lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic"))
    try:
        result = business_api.get_business_dashboard(company, "2026-10-01", "2026-10-31")
    finally:
        business_api.SECTION_LOADERS["sales"] = original
    assert result["partial"] is True
    assert result["sections"]["sales"] == {"available": False, "restricted": False, "cards": [],
                                             "message": "该模块暂时无法读取"}
    assert result["sections"]["purchases"]["available"] is True


def test_dashboard_exposes_expense_approval_and_project_delivery_metrics():
    from internal_ledger.api.business_api import get_business_dashboard

    company = frappe.flags.classification_company
    raw_metric_doc("Expense Claim", company=company, posting_date="2026-10-10", docstatus=0,
                   approval_status="Draft", total_claimed_amount=80)
    project = raw_metric_doc("Project", company=company, project_name="验收项目", status="Open",
                             percent_complete=40)
    raw_metric_doc("Task", company=company, project=project, subject="逾期任务", status="Open",
                   exp_end_date="2026-10-15")
    result = get_business_dashboard(company, "2026-10-01", "2026-10-31")
    assert card(result, "expenses", "待审批报销")["value"] == 1
    assert card(result, "projects", "项目平均进度")["value"] == 40
    assert card(result, "projects", "逾期任务")["value"] == 1
    assert card(result, "projects", "逾期任务")["target_route"] == "/app/task"


def test_dashboard_has_a_bounded_query_count():
    from internal_ledger.api.business_api import get_business_dashboard

    company = frappe.flags.classification_company
    calls = 0
    original = frappe.db.sql

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    frappe.db.sql = counted
    try:
        get_business_dashboard(company, "2026-10-01", "2026-10-31")
    finally:
        frappe.db.sql = original
    assert calls <= 15


def load_tests(loader, tests, pattern):
    def business_set_up():
        frappe.set_user("Administrator")
        set_up()

    def business_tear_down():
        frappe.set_user("Administrator")
        for doctype, name in reversed(CREATED):
            frappe.db.delete(doctype, {"name": name})
        CREATED.clear()
        tear_down()

    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=business_set_up, tearDown=business_tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
