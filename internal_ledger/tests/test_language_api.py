import unittest

import frappe

from internal_ledger.tests.test_member_api import member


def language_setter():
    try:
        from internal_ledger.api.language_api import set_language
    except ModuleNotFoundError as error:
        raise AssertionError("language preference API is not implemented") from error
    return set_language


def test_language_preference_is_saved_per_user():
    set_language = language_setter()

    user = member().name
    try:
        frappe.set_user(user)
        assert set_language("zh") == {"language": "zh"}
        assert frappe.db.get_value("User", user, "language") == "zh"
        assert set_language("en") == {"language": "en"}
        assert frappe.db.get_value("User", user, "language") == "en"
    finally:
        frappe.set_user("Administrator")


def test_language_preference_rejects_guest_and_unsupported_values():
    set_language = language_setter()

    frappe.set_user("Guest")
    try:
        with unittest.TestCase().assertRaises(frappe.AuthenticationError):
            set_language("zh")
    finally:
        frappe.set_user("Administrator")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        set_language("fr")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([
        unittest.FunctionTestCase(test_language_preference_is_saved_per_user),
        unittest.FunctionTestCase(test_language_preference_rejects_guest_and_unsupported_values),
    ])
