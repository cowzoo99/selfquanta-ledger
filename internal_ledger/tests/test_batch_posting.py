import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe

from internal_ledger.tests.test_classifier import account, tear_down
from internal_ledger.tests.test_review_api import draft as first_draft, set_up


def draft():
    if not frappe.db.exists("Classification Rule", {"company": frappe.flags.classification_company}):
        return first_draft()
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft
    from internal_ledger.tests.test_classifier import transaction
    name = transaction()
    return frappe.get_doc("Voucher Draft", create_draft(name, classify(name)))


def batch(names):
    from internal_ledger.accounting.batch_workflow import approve_and_post
    return approve_and_post(names)


def assert_posted(row):
    assert row["status"] == "created"
    assert frappe.db.get_value("Voucher Draft", row["draft"], "status") == "Posted"
    journal = frappe.get_doc("Journal Entry", row["journal_entry"])
    assert journal.docstatus == 1
    assert sum(Decimal(str(line.debit)) for line in journal.accounts) == Decimal("12.5")
    assert sum(Decimal(str(line.credit)) for line in journal.accounts) == Decimal("12.5")
    assert frappe.db.count("Posting Link", {"voucher_draft": row["draft"]}) == 1
    link = frappe.get_doc("Posting Link", row["posting_link"])
    assert (link.voucher_draft, link.journal_entry) == (row["draft"], row["journal_entry"])


def test_partial_success_closed_period_and_repairable_failed_draft():
    from internal_ledger.api.review_api import update_draft
    good, closed, later = [draft() for _ in range(3)]
    update_draft(closed.name, [line.as_dict() for line in closed.lines], "关闭期间测试", "2026-10-02")
    period = frappe.get_doc({"doctype": "Accounting Period", "period_name": uuid4().hex,
        "company": good.company, "start_date": "2026-10-02", "end_date": "2026-10-02",
        "closed_documents": [{"document_type": "Journal Entry", "closed": 1}]}).insert()
    rows = batch([good.name, closed.name, later.name])
    assert [row["name"] for row in rows] == [good.name, closed.name, later.name]
    assert [row["status"] for row in rows] == ["created", "failed", "created"]
    assert_posted(rows[0])
    assert_posted(rows[2])
    assert frappe.db.get_value("Voucher Draft", closed.name, "status") == "Draft"
    assert not frappe.db.exists("Posting Link", {"voucher_draft": closed.name})
    assert frappe.db.count("Journal Entry", {"company": good.company}) == 2
    assert not frappe.db.exists("Internal Ledger Audit Event", {"reference_name": closed.name, "action": "approve_draft", "result": "success"})
    assert frappe.db.exists("Internal Ledger Audit Event", {"reference_name": closed.name, "action": "approve_and_post", "result": "failed"})
    frappe.db.set_value("Accounting Period", period.name, "disabled", 1)
    assert_posted(batch([closed.name])[0])


def test_approved_input_posts_directly_and_repeats_return_existing_references():
    from internal_ledger.api.review_api import approve_draft
    doc = approve_draft(draft().name)
    rows = batch([doc.name, doc.name])
    assert_posted(rows[0])
    assert rows[1]["status"] == "skipped"
    for field in ("draft", "journal_entry", "posting_link"):
        assert rows[1][field] == rows[0][field]
    again = batch([doc.name])[0]
    assert again["status"] == "skipped" and again["journal_entry"] == rows[0]["journal_entry"]
    assert frappe.db.count("Journal Entry", {"company": doc.company}) == 1
    assert frappe.db.count("Internal Ledger Audit Event", {"reference_name": doc.name, "action": "approve_draft"}) == 1
    assert frappe.db.count("Internal Ledger Audit Event", {"reference_name": doc.name, "action": "post_draft"}) == 1


def test_posted_without_link_and_link_with_wrong_draft_status_fail():
    doc = draft()
    frappe.db.set_value("Voucher Draft", doc.name, "status", "Posted")
    assert batch([doc.name])[0]["status"] == "failed"
    frappe.db.set_value("Voucher Draft", doc.name, "status", "Draft")
    posted = batch([doc.name])[0]
    assert_posted(posted)
    frappe.db.set_value("Voucher Draft", doc.name, "status", "Draft")
    assert batch([doc.name])[0]["status"] == "failed"
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft"
    assert frappe.db.count("Journal Entry", {"company": doc.company}) == 1


def test_posted_retry_refuses_cancelled_or_foreign_journal():
    doc = draft()
    row = batch([doc.name])[0]
    assert_posted(row)
    for field, invalid, valid in (("docstatus", 2, 1), ("company", "_Test Company", doc.company)):
        frappe.db.set_value("Journal Entry", row["journal_entry"], field, invalid)
        assert batch([doc.name])[0]["status"] == "failed"
        frappe.db.set_value("Journal Entry", row["journal_entry"], field, valid)


def test_rejected_tampered_unbalanced_and_disabled_accounts_stay_unposted():
    from internal_ledger.api.review_api import reject_draft
    rejected, tampered, unbalanced, disabled = [draft() for _ in range(4)]
    reject_draft(rejected.name, "请核对")
    frappe.db.set_value("Voucher Draft", tampered.name, "transaction_type", "income")
    frappe.db.set_value("Voucher Draft Line", unbalanced.lines[0].name, "debit", 12.49)
    frappe.db.set_value("Account", account("560201"), "disabled", 1)
    rows = batch([disabled.name])
    frappe.db.set_value("Account", account("560201"), "disabled", 0)
    rows += batch([rejected.name, tampered.name, unbalanced.name])
    assert all(row["status"] == "failed" for row in rows)
    assert frappe.db.get_value("Voucher Draft", rejected.name, "status") == "Rejected"
    assert all(frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft" for doc in (tampered, unbalanced, disabled))
    assert frappe.db.count("Journal Entry", {"company": rejected.company}) == 0
    assert frappe.db.count("Posting Link", {"voucher_draft": ["in", [doc.name for doc in (rejected, tampered, unbalanced, disabled)]]}) == 0


def test_frozen_company_and_account_are_refused():
    doc = draft()
    frappe.db.set_value("Company", doc.company, "accounts_frozen_till_date", "2026-10-01")
    assert batch([doc.name])[0]["status"] == "failed"
    frappe.db.set_value("Company", doc.company, "accounts_frozen_till_date", None)
    frappe.db.set_value("Account", account("560201"), "freeze_account", "Yes")
    assert batch([doc.name])[0]["status"] == "failed"
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft"
    assert not frappe.db.exists("Posting Link", {"voucher_draft": doc.name})


def test_post_insert_failure_rolls_back_journal_gl_link_and_approval_and_redacts_messages():
    from internal_ledger.internal_ledger.doctype.posting_link.posting_link import PostingLink
    doc, later = draft(), draft()
    original = PostingLink.insert
    def fail(link, *args, **kwargs):
        result = original(link, *args, **kwargs)
        if link.voucher_draft == doc.name:
            frappe.throw("private-token-DO-NOT-LEAK")
        return result
    frappe.msgprint("此前安全提示")
    prior = list(frappe.local.message_log)
    with patch.object(PostingLink, "insert", fail):
        rows = batch([doc.name, later.name])
    assert rows[0]["status"] == "failed"
    assert_posted(rows[1])
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft"
    assert not frappe.db.exists("Posting Link", {"voucher_draft": doc.name})
    assert frappe.db.count("Journal Entry", {"company": doc.company}) == 1
    assert frappe.db.count("GL Entry", {"company": doc.company}) == 2
    assert "private-token" not in str(rows)
    assert frappe.local.message_log == prior
    events = frappe.get_all("Internal Ledger Audit Event", filters={"reference_name": doc.name}, fields=["*"])
    assert "private-token" not in str(events)
    assert [(row.action, row.result) for row in events if row.action in {"approve_draft", "post_draft", "approve_and_post"}] == [("approve_and_post", "failed")]


def test_viewer_denied_by_service_and_post_only_json_api():
    from internal_ledger.api.posting_api import approve_and_post
    from internal_ledger.tests.test_permissions import user
    doc = draft()
    viewer = user("账务查看者")
    try:
        frappe.set_user(viewer)
        for operation in (lambda: batch([doc.name]), lambda: approve_and_post(frappe.as_json([doc.name]))):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
    finally:
        frappe.set_user("Administrator")
    assert frappe.allowed_http_methods_for_whitelisted_func[approve_and_post] == ["POST"]
    assert_posted(approve_and_post(frappe.as_json([doc.name]))[0])


def test_document_permissions_fail_only_denied_row():
    from internal_ledger.internal_ledger.doctype.voucher_draft.voucher_draft import VoucherDraft
    denied, good = draft(), draft()
    original = VoucherDraft.check_permission
    for permission in ("read", "write"):
        def deny(doc, permtype="read", *args, **kwargs):
            if doc.name == denied.name and permtype == permission:
                raise frappe.PermissionError("private-token")
            return original(doc, permtype, *args, **kwargs)
        with patch.object(VoucherDraft, "check_permission", deny):
            assert batch([denied.name])[0]["status"] == "failed"
    assert_posted(batch([good.name])[0])
    assert frappe.db.get_value("Voucher Draft", denied.name, "status") == "Draft"


def test_journal_create_or_submit_permission_denial_rolls_back_approval():
    from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
    doc = draft()
    original = JournalEntry.check_permission
    for permission in ("create", "submit"):
        def deny(journal, permtype="read", *args, **kwargs):
            if permtype == permission:
                raise frappe.PermissionError("no journal permission")
            return original(journal, permtype, *args, **kwargs)
        with patch.object(JournalEntry, "check_permission", deny):
            assert batch([doc.name])[0]["status"] == "failed"
        assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft"
        assert not frappe.db.exists("Posting Link", {"voucher_draft": doc.name})
        assert frappe.db.count("Journal Entry", {"company": doc.company}) == 0


def test_savepoints_released_and_invalid_inputs_have_safe_outcomes():
    doc = draft()
    with patch.object(frappe.db, "savepoint", wraps=frappe.db.savepoint) as saved, patch.object(
            frappe.db, "release_savepoint", wraps=frappe.db.release_savepoint) as released:
        rows = batch([doc.name, "missing-draft", None, 42, {"secret": "private-token"}])
    points = [call.args[0] for call in saved.call_args_list if call.args[0].startswith("ledger_post_")]
    assert len(points) == 5
    assert [call.args[0] for call in released.call_args_list if call.args[0].startswith("ledger_post_")] == points
    assert [row["name"] for row in rows] == [doc.name, "missing-draft", "", "", ""]
    assert [row["status"] for row in rows] == ["created", "failed", "failed", "failed", "failed"]
    assert "private-token" not in str(rows)
    for value in ("invalid", {}, None):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            batch(value)


def test_deadlock_propagates_without_rollback_to_invalid_savepoint():
    from internal_ledger.accounting import posting
    doc = draft()
    with patch.object(posting, "post_draft", side_effect=frappe.QueryDeadlockError("deadlock")), patch.object(
            frappe.db, "rollback", wraps=frappe.db.rollback) as rollback:
        with unittest.TestCase().assertRaises(frappe.QueryDeadlockError):
            batch([doc.name])
    assert not any(call.kwargs.get("save_point", "").startswith("ledger_post_") for call in rollback.call_args_list)


def test_real_ledger_administrator_posts_and_audits_actor():
    from internal_ledger.tests.test_permissions import user
    doc = draft()
    admin = user("账套管理员")
    try:
        frappe.set_user(admin)
        row = batch([doc.name])[0]
        assert_posted(row)
        assert frappe.db.exists("Internal Ledger Audit Event", {"reference_name": doc.name, "action": "approve_draft", "actor": admin, "result": "success"})
        assert frappe.db.exists("Internal Ledger Audit Event", {"reference_name": doc.name, "action": "post_draft", "actor": admin, "result": "success"})
    finally:
        frappe.set_user("Administrator")


def test_failed_approved_draft_remains_approved_and_can_retry():
    from internal_ledger.api.review_api import approve_draft
    doc = approve_draft(draft().name)
    frappe.db.set_value("Account", account("560201"), "freeze_account", "Yes")
    assert batch([doc.name])[0]["status"] == "failed"
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Approved"
    frappe.db.set_value("Account", account("560201"), "freeze_account", "No")
    assert_posted(batch([doc.name])[0])

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
