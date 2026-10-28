import base64
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import frappe

from internal_ledger.tests.test_classifier import account, transaction
from internal_ledger.tests.test_importers import fixture
from internal_ledger.tests.test_review_api import draft, set_up


def user(role):
    name = f"ledger-{uuid4().hex}@example.com"
    frappe.get_doc({"doctype": "User", "email": name, "first_name": "Ledger test",
                    "send_welcome_email": 0, "roles": [{"role": role}]}).insert()
    return name


def test_non_ledger_system_manager_cannot_call_import_service():
    from internal_ledger.importing.models import save_batch
    from internal_ledger.importing.registry import parse_statement

    result = parse_statement("bank", fixture("bank"), account("1001"))
    manager = user("System Manager")
    frappe.set_user(manager)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        save_batch("bank", result.file_hash, result.rows)


def test_install_write_services_require_ledger_admin():
    from internal_ledger.install import ensure_roles, install_chart

    company = frappe.flags.classification_company
    actors = (user("账务查看者"), user("System Manager"), "Guest")
    for actor in actors:
        frappe.set_user(actor)
        for operation in (ensure_roles, lambda: install_chart(company)):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()


def test_viewer_and_guest_all_write_entrypoints_are_denied():
    from internal_ledger.accounting.drafts import create_draft
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting
    from internal_ledger.api import import_api, posting_api, review_api
    from internal_ledger.importing.models import save_batch
    from internal_ledger.importing.registry import parse_statement

    doc = draft()
    classification = classify(doc.imported_transaction)
    encoded = base64.b64encode(fixture("bank")).decode()
    result = parse_statement("bank", fixture("bank"), account("1001"))
    viewer = user("账务查看者")
    operations = [lambda: import_api.preview_import("bank", encoded, "test"),
                  lambda: import_api.confirm_import("bank", encoded, "test", result.file_hash),
                  lambda: save_batch("bank", result.file_hash, result.rows),
                  lambda: create_draft(doc.imported_transaction, classification),
                  lambda: review_api.prepare_draft(doc.imported_transaction),
                  lambda: review_api.update_draft(doc.name, [], "test", "2026-10-01"),
                  lambda: review_api.approve_draft(doc.name),
                  lambda: review_api.reject_draft(doc.name, "test"),
                  lambda: review_api.reopen_draft(doc.name),
                  lambda: post_draft(doc.name), lambda: reverse_posting("missing", "test"),
                  lambda: posting_api.post_draft(doc.name),
                  lambda: posting_api.reverse_posting("missing", "test")]
    for actor in (viewer, "Guest", user("System Manager")):
        frappe.set_user(actor)
        for operation in operations:
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()


def test_roles_and_custom_permissions_are_idempotent_and_viewer_read_only():
    from internal_ledger.install import ensure_roles

    ensure_roles()
    ensure_roles()
    viewer = user("账务查看者")
    admin = user("账套管理员")
    for doctype in ("Import Batch", "Imported Transaction", "Classification Rule", "Voucher Draft",
                    "Posting Link", "Internal Ledger Settings", "Internal Ledger Audit Event"):
        assert frappe.has_permission(doctype, "read", user=viewer)
        assert frappe.has_permission(doctype, "report", user=viewer)
        for action in ("create", "write", "delete", "submit", "cancel"):
            assert not frappe.has_permission(doctype, action, user=viewer), (doctype, action)
    for action in ("create", "write", "submit", "cancel", "delete"):
        assert not frappe.has_permission("Journal Entry", action, user=viewer)
    for action in ("read", "write", "create", "submit"):
        assert frappe.has_permission("Journal Entry", action, user=admin)
    for action in ("cancel", "delete"):
        assert not frappe.has_permission("Journal Entry", action, user=admin)
    assert frappe.db.count("Custom DocPerm", {"parent": "Journal Entry", "role": "账套管理员",
                                             "permlevel": 0}) == 1


def test_viewer_reads_filtered_company_and_sensitive_fields():
    from internal_ledger.api.review_api import get_review_item, list_review_items, get_dashboard_counts

    doc = draft()
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft

    other_tx = transaction()
    other = frappe.get_doc("Voucher Draft", create_draft(other_tx, classify(other_tx)))
    pending = transaction()
    frappe.db.set_value("Voucher Draft", other.name, "company", "_Test Company")
    frappe.db.set_value("Import Batch", frappe.db.get_value("Imported Transaction", other.imported_transaction,
                                                          "import_batch"), "company", "_Test Company")
    viewer = user("账务查看者")
    frappe.set_user(viewer)
    item = get_review_item(doc.name)
    assert item["draft"]["name"] == doc.name
    assert "source_id" not in item["transaction"] and "dedupe_key" not in item["transaction"]
    assert {row["draft"]["name"] for row in list_review_items() if row["draft"]} == {doc.name}
    pending_row = next(row for row in list_review_items() if row["transaction"]["name"] == pending)
    assert pending_row["draft"] is None and "source_id" not in pending_row["transaction"]
    assert "dedupe_key" not in pending_row["transaction"]
    assert get_dashboard_counts("2026-10")["pending_review"] == 1
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        get_review_item(other.name)


def test_audit_only_system_creation_append_only_and_transactional():
    from internal_ledger.permissions import record_audit
    from internal_ledger.api.review_api import approve_draft

    doc = draft()
    admin = user("账套管理员")
    viewer = user("账务查看者")
    frappe.set_user(admin)
    frappe.db.savepoint("audit_atomic")
    approve_draft(doc.name)
    events = frappe.get_all("Internal Ledger Audit Event", filters={"action": "approve_draft",
                             "reference_name": doc.name}, fields=["*"])
    assert len(events) == 1
    event = events[0]
    assert event.actor == admin and event.result == "success" and event.timestamp
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        frappe.get_doc({"doctype": "Internal Ledger Audit Event", "actor": admin, "action": "forged",
                        "reference_doctype": "Voucher Draft", "reference_name": doc.name,
                        "result": "success"}).insert(ignore_permissions=True)
    saved = frappe.get_doc("Internal Ledger Audit Event", event.name)
    saved.result = "changed"
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        saved.save(ignore_permissions=True)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        saved.delete(ignore_permissions=True)
    frappe.db.rollback(save_point="audit_atomic")
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft"
    assert not frappe.db.exists("Internal Ledger Audit Event", event.name)
    frappe.set_user(viewer)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        record_audit("forged", "Voucher Draft", doc.name, "success")


def test_audit_failure_rolls_back_business_and_all_write_actions_are_recorded():
    from internal_ledger.api.review_api import update_draft, reject_draft, reopen_draft, approve_draft
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting
    from internal_ledger.internal_ledger.doctype.internal_ledger_audit_event.internal_ledger_audit_event import InternalLedgerAuditEvent

    doc = draft()
    admin = user("账套管理员")
    frappe.set_user(admin)
    frappe.db.savepoint("audit_failure")
    with patch.object(InternalLedgerAuditEvent, "before_insert", side_effect=RuntimeError("audit unavailable")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "audit unavailable"):
            approve_draft(doc.name)
    frappe.db.rollback(save_point="audit_failure")
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft"
    assert not frappe.db.count("Internal Ledger Audit Event", {"actor": admin})
    update_draft(doc.name, [row.as_dict() for row in doc.lines], "核对", "2026-10-01")
    reject_draft(doc.name, "核对")
    reopen_draft(doc.name)
    approve_draft(doc.name)
    journal = post_draft(doc.name)
    link = frappe.db.get_value("Posting Link", {"journal_entry": journal}, "name")
    reverse_posting(link, "核对")
    actions = frappe.get_all("Internal Ledger Audit Event", filters={"actor": admin}, pluck="action")
    assert sorted(actions) == sorted(["update_draft", "reject_draft", "reopen_draft", "approve_draft",
                                      "post_draft", "reverse_posting"])
    print("AUDIT EVIDENCE", doc.name, journal, "6 actions; audit insertion failure rolls approval back")


def test_private_source_is_immutable_and_viewer_cannot_list_or_download():
    from internal_ledger.api.import_api import confirm_import, preview_import
    from frappe.handler import download_file
    from frappe.core.doctype.file.utils import find_file_by_url

    encoded = base64.b64encode(fixture("bank")).decode()
    preview = preview_import("bank", encoded, account("1001"))
    name = confirm_import("bank", encoded, account("1001"), preview["file_hash"])["batch_name"]
    batch = frappe.get_doc("Import Batch", name)
    source = frappe.get_doc("Internal Ledger Source File", batch.raw_file)
    assert source.raw_file.startswith("/private/files/")
    attachment = find_file_by_url(source.raw_file)
    assert attachment and attachment.is_private
    assert attachment.attached_to_doctype == "Internal Ledger Source File"
    assert Path(attachment.get_full_path()).read_bytes() == fixture("bank")
    download_file(source.raw_file)
    assert frappe.local.response.filecontent.encode() == fixture("bank")
    original_url = source.raw_file
    batch.file_hash = "forged"
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        batch.save()
    attachment.file_url = "/private/files/replaced.csv"
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        attachment.save(ignore_permissions=True)
    frappe.set_user(user("账务查看者"))
    from frappe.client import get

    assert not get("Import Batch", name).get("raw_file")
    assert not find_file_by_url(original_url)
    assert not frappe.has_permission("Internal Ledger Source File", "read", attachment.attached_to_name)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        frappe.get_list("Internal Ledger Source File", filters={"name": source.name})
    assert not frappe.has_permission("File", "read", attachment.name)
    for denied in (frappe.get_doc("File", attachment.name),
                   frappe.get_doc("Internal Ledger Source File", source.name)):
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            denied.check_permission("read")
    assert not frappe.get_list("File", filters={"name": attachment.name}, fields=["name"])
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        download_file(original_url)
    from frappe.utils.response import download_private_file
    from werkzeug.exceptions import Forbidden

    with unittest.TestCase().assertRaises(Forbidden):
        download_private_file(original_url)
    print("SOURCE EVIDENCE", source.name, attachment.name, "admin download; viewer list/read/API+private URL denied")


def test_native_reporting_and_balances_cannot_read_another_company():
    from internal_ledger.api.report_api import get_account_balance
    from internal_ledger.install import install_chart
    from erpnext.accounts.report.general_ledger.general_ledger import execute

    other = frappe.get_doc({"doctype": "Company", "company_name": "_Test Ledger Other",
        "abbr": "ILOT", "default_currency": "CNY", "country": "China",
        "create_chart_of_accounts_based_on": "Standard Template", "chart_of_accounts": "Standard"}).insert()
    install_chart(other.name)
    foreign_cash = frappe.db.get_value("Account", {"company": other.name, "account_number": "1001"}, "name")
    foreign_equity = frappe.db.get_value("Account", {"company": other.name, "account_number": "3001"}, "name")
    journal = frappe.get_doc({"doctype": "Journal Entry", "company": other.name, "posting_date": "2026-10-01",
        "accounts": [{"account": foreign_cash, "debit_in_account_currency": 1},
                     {"account": foreign_equity, "credit_in_account_currency": 1}]}).insert()
    journal.submit()
    native_balance = frappe.get_attr(frappe.override_whitelisted_method("erpnext.accounts.utils.get_balance_on"))
    for actor in (user("账务查看者"), user("账套管理员")):
        frappe.set_user(actor)
        for doctype in ("Account", "Journal Entry", "GL Entry"):
            assert not frappe.get_list(doctype, filters={"company": other.name}, fields=["name"])
            doc = frappe.get_doc(doctype, frappe.db.get_value(doctype, {"company": other.name}, "name"))
            assert not frappe.has_permission(doctype, "read", doc)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            get_account_balance(foreign_cash, "2026-10-31")
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            native_balance(company=other.name, ignore_account_permission=True)
        assert native_balance(account=account("1001"), date="2026-10-31") == 0
        result = execute(frappe._dict(company=other.name, from_date="2026-10-01", to_date="2026-10-31",
                                     group_by="Group by Voucher (Consolidated)"))
        assert not any(row.get("voucher_no") == journal.name for row in result[1])


def tear_down():
    frappe.set_user("Administrator")
    paths = [Path(frappe.get_doc("File", row.name).get_full_path()) for row in frappe.get_all(
        "File", filters={"attached_to_doctype": "Internal Ledger Source File"}, fields=["name"])]
    # Native File rollback watchers run only on full rollback, never a savepoint rollback.
    frappe.db.rollback()
    assert all(not path.exists() for path in paths)
    frappe.clear_cache()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
