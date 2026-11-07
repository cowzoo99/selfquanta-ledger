import unittest
from pathlib import Path
from uuid import uuid4

import frappe

from internal_ledger.tests.test_business_workflows import account, tear_down
from internal_ledger.tests.test_review_api import set_up


def _hr_fixture():
    from frappe.permissions import add_user_permission
    from erpnext.setup.doctype.employee.test_employee import make_employee
    from hrms.hr.doctype.attendance.attendance import mark_attendance

    company = frappe.flags.classification_company
    suffix = uuid4().hex[:8]
    expense, payable = account("560201"), account("2202")
    frappe.db.set_value("Account", payable, "account_type", "Payable")
    cost_center = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")
    assert cost_center
    company_doc = frappe.get_doc("Company", company)
    assignment = frappe.db.get_value("Holiday List Assignment", {
        "applicable_for": "Company", "assigned_to": company, "docstatus": 1,
    }, ["name", "holiday_list"], as_dict=True)
    holiday_list = assignment.holiday_list if assignment else frappe.get_doc({
        "doctype": "Holiday List", "holiday_list_name": "测试假日表-" + suffix,
        "from_date": "2026-01-01", "to_date": "2026-12-31",
    }).insert().name
    company_doc.default_payroll_payable_account = payable
    company_doc.default_holiday_list = holiday_list
    company_doc.cost_center = cost_center
    company_doc.save()
    if not assignment:
        frappe.get_doc({
            "doctype": "Holiday List Assignment", "applicable_for": "Company",
            "assigned_to": company, "holiday_list": holiday_list, "from_date": "2026-01-01",
        }).insert().submit()

    designation = frappe.get_doc({
        "doctype": "Designation", "designation_name": "测试岗位-" + suffix,
    }).insert().name
    department = frappe.get_doc({
        "doctype": "Department", "department_name": "测试部门-" + suffix,
        "company": company,
    }).insert().name
    emails = [f"employee-{suffix}@example.com", f"employee-other-{suffix}@example.com"]
    employees = [make_employee(email, company=company, department=department, designation=designation)
                 for email in emails]
    for email, employee in zip(emails, employees):
        add_user_permission("Employee", employee, email)
        add_user_permission("Company", company, email)

    attendance = mark_attendance(employees[0], "2026-10-05", "Present")
    leave_type = frappe.get_doc({
        "doctype": "Leave Type", "leave_type_name": "测试无薪假-" + suffix,
        "is_lwp": 1, "include_holiday": 1,
    }).insert().name
    leave = frappe.get_doc({
        "doctype": "Leave Application", "employee": employees[0], "company": company,
        "leave_type": leave_type, "from_date": "2026-10-06", "to_date": "2026-10-06",
        "status": "Open", "description": "员工自助请假",
    }).insert()

    component_name = "基本工资-" + suffix
    component = frappe.get_doc({
        "doctype": "Salary Component", "salary_component": component_name,
        "salary_component_abbr": ("BG" + suffix)[:8].upper(), "type": "Earning",
        "depends_on_payment_days": 0,
        "accounts": [{"company": company, "account": expense}],
    }).insert()
    structure = frappe.get_doc({
        "doctype": "Salary Structure", "name": "测试工资结构-" + suffix,
        "company": company, "payroll_frequency": "Monthly", "currency": "CNY",
        "earnings": [{"salary_component": component.name, "amount": 5000}],
    }).insert()
    structure.submit()
    assignment = frappe.get_doc({
        "doctype": "Salary Structure Assignment", "employee": employees[0],
        "salary_structure": structure.name, "from_date": "2026-01-01", "company": company,
        "currency": "CNY", "payroll_payable_account": payable, "base": 5000,
    }).insert()
    assignment.submit()
    payroll = frappe.get_doc({
        "doctype": "Payroll Entry", "company": company, "posting_date": "2026-10-31",
        "start_date": "2026-10-01", "end_date": "2026-10-31", "payroll_frequency": "Monthly",
        "payroll_payable_account": payable, "currency": "CNY", "exchange_rate": 1,
        "cost_center": cost_center, "employees": [{"employee": employees[0]}],
    }).insert()
    return frappe._dict(company=company, department=department, emails=emails, employees=employees,
                        attendance=attendance, leave=leave.name, payroll=payroll.name)


def test_employee_self_service_payroll_confidentiality_and_standard_accounting_flow():
    fixture = _hr_fixture()
    payroll = frappe.get_doc("Payroll Entry", fixture.payroll)
    journal_filters = {"reference_type": "Payroll Entry", "reference_name": payroll.name, "docstatus": 1}
    assert not frappe.get_all("Journal Entry Account", journal_filters, pluck="parent", distinct=True)
    payroll.submit()
    slips = frappe.get_all("Salary Slip", {"payroll_entry": payroll.name}, pluck="name")
    payroll.reload()
    assert len(slips) == 1, f"工资单生成失败：{payroll.status} {payroll.error_message}"
    slip = frappe.get_doc("Salary Slip", slips[0])
    assert slip.docstatus == 0
    assert slip.gross_pay == 5000, [(row.salary_component, row.amount) for row in slip.earnings]
    payroll.submit_salary_slips()
    assert frappe.db.get_value("Salary Slip", slips[0], "docstatus") == 1
    journal_entry = frappe.db.get_value("Salary Slip", slips[0], "journal_entry")
    payroll.reload()
    assert journal_entry and frappe.db.get_value("Journal Entry", journal_entry, "docstatus") == 1, \
        f"工资会计分录生成失败：{payroll.status} {payroll.error_message}"
    assert frappe.db.count("GL Entry", {"voucher_type": "Journal Entry", "voucher_no": journal_entry,
                                        "is_cancelled": 0}) >= 2
    assert frappe.db.get_value("Attendance", fixture.attendance, "docstatus") == 1
    assert frappe.db.get_value("Leave Application", fixture.leave, "employee") == fixture.employees[0]
    assert frappe.db.get_value("Employee", fixture.employees[0], "department") == fixture.department

    frappe.set_user(fixture.emails[0])
    assert "System Manager" not in frappe.get_roles()
    assert frappe.has_permission("Employee", "read", fixture.employees[0])
    assert not frappe.has_permission("Employee", "read", fixture.employees[1])
    assert frappe.has_permission("Salary Slip", "read", slips[0])
    assert not frappe.has_permission("Payroll Entry", "write", payroll.name)


def test_private_hr_attachments_are_included_in_the_encrypted_site_backup_chain():
    script = (Path(__file__).resolve().parents[2] / "infra" / "backup.ps1").read_text(encoding="utf-8")
    assert "--with-files" in script
    assert "--backup-path-private-files" in script
    assert "private-files.tgz" in script


def load_tests(loader, tests, pattern):
    def hr_set_up():
        frappe.set_user("Administrator")
        frappe.flags.hr_previous_ledger_settings = {
            field: frappe.db.get_single_value("Internal Ledger Settings", field)
            for field in ("company", "base_currency", "fiscal_year_start")
        }
        set_up()

    def hr_tear_down():
        frappe.set_user("Administrator")
        previous = frappe.flags.hr_previous_ledger_settings
        company = frappe.flags.classification_company
        try:
            tear_down()
        finally:
            frappe.db.delete("Expense Claim Account", {"company": company})
            for field, value in previous.items():
                frappe.db.set_single_value("Internal Ledger Settings", field, value)
            frappe.db.commit()
            frappe.clear_cache()

    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=hr_set_up, tearDown=hr_tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
