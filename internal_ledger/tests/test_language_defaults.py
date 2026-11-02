import unittest
from uuid import uuid4

import frappe

from internal_ledger.tests.test_member_api import MEMBER_PASSWORD, member_company_reference


def test_new_enterprise_member_defaults_to_chinese():
    from internal_ledger.api.member_api import create_enterprise_member

    frappe.set_user("Administrator")
    previous_company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    company = member_company_reference()
    frappe.db.set_single_value("Internal Ledger Settings", "company", company)
    try:
        email = f"chinese-member-{uuid4().hex}@example.invalid"
        create_enterprise_member("中文成员", email, MEMBER_PASSWORD, MEMBER_PASSWORD)
        assert frappe.db.get_value("User", email, "language") == "zh"
    finally:
        frappe.db.set_single_value("Internal Ledger Settings", "company", previous_company)


def test_install_sets_system_default_language_to_chinese():
    from internal_ledger.install import ensure_roles

    frappe.set_user("Administrator")
    previous_language = frappe.db.get_single_value("System Settings", "language")
    frappe.db.set_single_value("System Settings", "language", "en")
    try:
        ensure_roles()
        assert frappe.db.get_single_value("System Settings", "language") == "zh"
    finally:
        frappe.db.set_single_value("System Settings", "language", previous_language)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([
        unittest.FunctionTestCase(test_new_enterprise_member_defaults_to_chinese),
        unittest.FunctionTestCase(test_install_sets_system_default_language_to_chinese),
    ])
