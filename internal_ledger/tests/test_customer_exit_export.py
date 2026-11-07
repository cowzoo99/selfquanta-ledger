import unittest

import frappe

from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_review_api import set_up, tear_down


def owner():
    return user("企业负责人")


def clean_up():
    frappe.set_user("Administrator")
    if frappe.db.table_exists("Customer Exit Export"):
        frappe.db.delete("Customer Exit Export")
    tear_down()


def test_only_explicit_enterprise_owner_passes_export_gate():
    from internal_ledger.permissions import require_enterprise_owner

    owner_user = owner()
    nonowners = (user("账套管理员"), user("账务查看者"), "Administrator", "Guest")
    frappe.set_user(owner_user)
    assert require_enterprise_owner() == frappe.session.user
    for actor in nonowners:
        frappe.set_user(actor)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            require_enterprise_owner()


def test_customer_exit_request_is_owner_read_only_and_service_written():
    from internal_ledger.customer_exit.service import _insert_request

    frappe.set_user(owner())
    request = _insert_request(frappe.session.user)
    assert request.status == "Generating"
    assert request.requested_by == frappe.session.user
    assert frappe.has_permission(request.doctype, "read", request)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        request.status = "Ready"
        request.save()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=clean_up)
        for name, test in sorted(globals().items())
        if name.startswith("test_")
    )
