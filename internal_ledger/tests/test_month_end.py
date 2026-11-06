import unittest
from unittest.mock import patch

import frappe

from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_review_api import draft, set_up, tear_down


EXPECTED_CODES = [
    "unposted_drafts",
    "pending_duplicates",
    "unreconciled_bank",
    "negative_stock",
    "unsubmitted_invoices",
    "unbalanced_accounts",
    "missing_payroll",
    "missing_tax_documents",
]


def clean_up():
    frappe.set_user("Administrator")
    company = frappe.flags.classification_company
    frappe.db.delete("Accounting Period", {"company": company})
    if frappe.db.table_exists("Month End Run"):
        frappe.db.delete("Month End Run", {"company": company})
    tear_down()


def test_check_codes_are_deterministic_and_identical_source_reuses_run():
    from internal_ledger.accounting.month_end import run_month_end_checks

    company = frappe.flags.classification_company
    first = run_month_end_checks(company, "2026-10")
    second = run_month_end_checks(company, "2026-10")
    assert [row["code"] for row in first["checks"]] == EXPECTED_CODES
    assert first["name"] == second["name"]
    assert first["source_hash"] == second["source_hash"]

    created = draft()
    changed = run_month_end_checks(company, "2026-10")
    assert changed["name"] != first["name"]
    assert changed["source_hash"] != first["source_hash"]
    check = next(row for row in changed["checks"] if row["code"] == "unposted_drafts")
    assert check["status"] == "Fail" and check["count"] == 1
    assert created.name in check["target_route"]

    frappe.db.set_value("Voucher Draft", created.name, "summary", "同数量但内容已修改")
    modified = run_month_end_checks(company, "2026-10")
    assert modified["name"] != changed["name"]
    assert modified["source_hash"] != changed["source_hash"]


def test_blocking_check_refuses_close_and_viewer_is_read_only():
    from internal_ledger.api.close_api import confirm_period_close, get_month_end_run, run_month_end_checks

    draft()
    company = frappe.flags.classification_company
    result = run_month_end_checks(company, "2026-10")
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "阻断"):
        confirm_period_close(result["name"])

    viewer = user("账务查看者")
    frappe.set_user(viewer)
    assert get_month_end_run(result["name"])["name"] == result["name"]
    for operation in (lambda: run_month_end_checks(company, "2026-10"),
                      lambda: confirm_period_close(result["name"])):
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            operation()


def test_clean_run_closes_standard_accounting_period_and_audits():
    from internal_ledger.accounting import month_end
    from internal_ledger.api.close_api import confirm_period_close

    company = frappe.flags.classification_company
    checks = {
        code: (lambda start, end, company, code=code: month_end.CheckResult(
            code, "Warning", "Pass", 0, "检查通过", f"/app/{code}"
        ))
        for code in EXPECTED_CODES
    }
    with patch.object(month_end, "CHECKS", checks):
        result = month_end.run_month_end_checks(company, "2026-10")
    closed = confirm_period_close(result["name"])
    period = frappe.get_doc("Accounting Period", closed["accounting_period"])
    assert (str(period.start_date), str(period.end_date), period.company, period.disabled) == (
        "2026-10-01", "2026-10-31", company, 0,
    )
    assert {row.document_type for row in period.closed_documents} >= {
        "Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry",
    }
    assert frappe.db.get_value("Month End Run", result["name"], "status") == "Closed"
    for action in ("run_month_end_checks", "confirm_period_close"):
        assert frappe.db.count("Internal Ledger Audit Event", {
            "action": action, "reference_name": result["name"], "result": "success",
        }) == 1


def test_company_period_and_run_scope_are_enforced():
    from internal_ledger.accounting.month_end import run_month_end_checks
    from internal_ledger.api.close_api import get_month_end_run

    company = frappe.flags.classification_company
    for operation in (lambda: run_month_end_checks("_Test Company", "2026-10"),
                      lambda: run_month_end_checks(company, "2026-13")):
        with unittest.TestCase().assertRaises((frappe.PermissionError, frappe.ValidationError)):
            operation()
    with unittest.TestCase().assertRaises(frappe.DoesNotExistError):
        get_month_end_run("missing-run")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=clean_up)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
