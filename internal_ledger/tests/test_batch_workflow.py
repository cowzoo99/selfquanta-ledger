import unittest
from unittest.mock import patch

import frappe

from internal_ledger.accounting.classifier import classify
from internal_ledger.accounting.drafts import create_draft
from internal_ledger.tests.test_classifier import account, add_rule, tear_down, transaction
from uuid import uuid4
from internal_ledger.importing.models import save_batch
from internal_ledger.tests.test_dedupe import tx
from internal_ledger.tests.test_review_api import set_up


def duplicate_pair():
    source_id = uuid4().hex
    batch = save_batch("wechat", uuid4().hex, [tx(source_id=source_id, payment_account=account("1001")),
        tx(source_id=source_id, payment_account=account("1001"), raw_row=3)])
    return (frappe.get_doc("Imported Transaction", {"import_batch": batch, "status": status})
            for status in ("Imported", "Duplicate"))


def prepare(names):
    from internal_ledger.accounting.batch_workflow import prepare_matched_transactions
    return prepare_matched_transactions(names)


def test_process_transactions_reports_actionable_states_and_is_idempotent():
    from internal_ledger.accounting.batch_workflow import process_transactions
    add_rule()
    matched = transaction()
    unmatched = transaction(counterparty="无匹配商户")
    conflict = transaction(counterparty="冲突商户")
    add_rule(value="冲突商户")
    add_rule(value="冲突商户", debit_account=account("560202"))
    _, duplicate = duplicate_pair()

    outcomes = process_transactions([matched, unmatched, conflict, duplicate.name])
    assert [row["status"] for row in outcomes] == ["created", "manual", "conflict", "duplicate"]
    assert outcomes[0]["draft"]
    assert all(set(row) == {"name", "status", "draft", "message"} for row in outcomes)

    repeated = process_transactions([matched])
    assert repeated[0]["status"] == "skipped"
    assert repeated[0]["draft"] == outcomes[0]["draft"]
    assert frappe.db.count("Voucher Draft", {"imported_transaction": matched}) == 1


def test_mixed_batch_preserves_every_input_and_is_idempotent():
    add_rule()
    matched = transaction()
    unmatched = transaction(counterparty="无匹配商户")
    conflict = transaction(counterparty="冲突商户")
    add_rule(value="冲突商户")
    add_rule(value="冲突商户", debit_account=account("560202"))
    _, duplicate = duplicate_pair()
    drafted = transaction()
    existing = create_draft(drafted, classify(drafted))
    names = [matched, unmatched, conflict, duplicate.name, drafted, "missing-transaction", matched, ""]
    outcomes = prepare(names)
    assert [row["name"] for row in outcomes] == names
    assert [row["status"] for row in outcomes] == ["created", "manual", "conflict", "duplicate", "skipped", "failed", "skipped", "failed"]
    assert all(set(row) == {"name", "status", "draft", "message"} for row in outcomes)
    assert outcomes[4]["draft"] == existing
    assert outcomes[0]["draft"] == outcomes[6]["draft"]
    assert "人工分类" in outcomes[1]["message"] and "冲突" in outcomes[2]["message"]
    assert all(row["message"] for row in outcomes)
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": duplicate.name})
    assert frappe.db.count("Voucher Draft", {"company": frappe.flags.classification_company}) == 2
    repeated = prepare(names)
    assert repeated[0]["status"] == "skipped" and repeated[0]["draft"] == outcomes[0]["draft"]
    assert frappe.db.count("Voucher Draft", {"company": frappe.flags.classification_company}) == 2
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals
    assert all(row.status == "Draft" for row in frappe.get_all("Voucher Draft", filters={"company": frappe.flags.classification_company}, fields=["status"]))


def test_failure_rolls_back_only_its_row_and_redacts_exception():
    from internal_ledger.accounting import batch_workflow
    add_rule()
    good, broken, later = [transaction() for _ in range(3)]
    original = batch_workflow.create_draft
    def fail_after_insert(name, result):
        draft = original(name, result)
        if name == broken:
            raise RuntimeError("private-token-DO-NOT-LEAK")
        return draft
    with patch.object(batch_workflow, "create_draft", side_effect=fail_after_insert):
        outcomes = prepare([good, broken, later])
    assert [row["status"] for row in outcomes] == ["created", "failed", "created"]
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": broken})
    assert frappe.db.exists("Voucher Draft", outcomes[0]["draft"])
    assert frappe.db.exists("Voucher Draft", outcomes[2]["draft"])
    assert "private-token" not in str(outcomes)
    audit = frappe.get_all("Internal Ledger Audit Event", filters={"action": "prepare_draft", "reference_name": broken}, fields=["result"])
    assert [row.result for row in audit] == ["failed"]


def test_duplicate_states_report_duplicate_without_classifying_and_release_is_eligible():
    from internal_ledger.importing.duplicate_review import resolve
    from internal_ledger.accounting import batch_workflow
    _, pending = duplicate_pair()
    _, confirmed = duplicate_pair()
    resolve(confirmed.name, "confirm", "Administrator", "同一笔")
    _, released = duplicate_pair()
    resolve(released.name, "release", "Administrator", "真实交易")
    add_rule()
    original = batch_workflow.classify
    with patch.object(batch_workflow, "classify", wraps=original) as classifier:
        outcomes = prepare([pending.name, confirmed.name, released.name])
    assert [row["status"] for row in outcomes] == ["duplicate", "duplicate", "created"]
    assert [call.args[0] for call in classifier.call_args_list] == [released.name]


def test_other_non_imported_rows_are_skipped():
    name = transaction()
    frappe.db.set_value("Imported Transaction", name, "status", "Posted")
    assert prepare([name])[0]["status"] == "skipped"


def test_viewer_cannot_prepare_through_service_or_api():
    from internal_ledger.api.review_api import prepare_matched_transactions
    from internal_ledger.tests.test_permissions import user
    name = transaction()
    viewer = user("账务查看者")
    try:
        frappe.set_user(viewer)
        for operation in (lambda: prepare([name]), lambda: prepare_matched_transactions([name])):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
    finally:
        frappe.set_user("Administrator")
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": name})


def test_document_write_permission_blocks_one_row():
    add_rule()
    name = transaction()
    doc_type = type(frappe.get_doc("Imported Transaction", name))
    original = doc_type.check_permission
    def deny(doc, permtype="read", *args, **kwargs):
        if doc.doctype == "Imported Transaction" and doc.name == name and permtype == "write":
            raise frappe.PermissionError("denied")
        return original(doc, permtype, *args, **kwargs)
    with patch.object(doc_type, "check_permission", deny):
        assert prepare([name])[0]["status"] == "failed"
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": name})


def test_api_accepts_json_and_read_workbench_includes_duplicate_and_account_metadata():
    from internal_ledger.api.review_api import list_review_items, prepare_matched_transactions
    add_rule()
    name = transaction()
    _, duplicate = duplicate_pair()
    outcomes = prepare_matched_transactions(frappe.as_json([name]))
    assert outcomes[0]["status"] == "created"
    items = list_review_items()
    assert next(row for row in items if row["transaction"]["name"] == duplicate.name)["draft"] is None
    draft = next(row["draft"] for row in items if row["transaction"]["name"] == name)
    assert all(row["account_number"] and row["account_name"] for row in draft["lines"])
    from internal_ledger.api.review_api import get_dashboard_counts
    counts = get_dashboard_counts("2026-10")
    assert counts["pending_classification"] == 1  # original of the duplicate pair only


def test_invalid_container_is_rejected_before_any_write():
    for value in ("not-a-list", {}, None):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            prepare(value)






def test_non_string_names_return_safe_string_outcomes():
    outcomes = prepare([None, 42, {"secret": "private"}])
    assert [row["name"] for row in outcomes] == ["", "", ""]
    assert all(row["status"] == "failed" for row in outcomes)


def test_row_savepoints_are_released_after_success_and_failure():
    add_rule()
    with patch.object(frappe.db, "savepoint", wraps=frappe.db.savepoint) as saved, patch.object(
            frappe.db, "release_savepoint", wraps=frappe.db.release_savepoint) as released:
        prepare([transaction(), "missing-transaction"])
    points = [call.args[0] for call in saved.call_args_list if call.args[0].startswith("ledger_prepare_")]
    assert [call.args[0] for call in released.call_args_list if call.args[0].startswith("ledger_prepare_")] == points
    assert len(points) == 2


def test_database_deadlocks_propagate_without_using_invalid_savepoint():
    from internal_ledger.accounting import batch_workflow
    add_rule()
    name = transaction()
    with patch.object(batch_workflow, "classify", side_effect=frappe.QueryDeadlockError("deadlock")):
        with unittest.TestCase().assertRaises(frappe.QueryDeadlockError):
            prepare([name])



def test_validation_failures_do_not_leak_frappe_response_messages():
    from internal_ledger.accounting import batch_workflow
    add_rule()
    name = transaction()
    frappe.msgprint("此前安全提示")
    prior = list(frappe.local.message_log)
    def fail(name, result):
        frappe.throw("private-token-DO-NOT-LEAK")
    with patch.object(batch_workflow, "create_draft", side_effect=fail):
        outcome = prepare([name])[0]
    assert outcome["status"] == "failed"
    assert frappe.local.message_log == prior
    assert "private-token" not in str(outcome)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
