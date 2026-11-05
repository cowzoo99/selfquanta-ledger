import json
import unittest
from copy import deepcopy
from hashlib import sha256

import frappe

from internal_ledger.api import posting_api
from internal_ledger.tests.test_classifier import account, transaction
from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_posting import approved_draft, link_for
from internal_ledger.tests.test_review_api import draft, set_up


def posted_fixture():
    doc = approved_draft()
    journal = posting_api.post_draft(doc.name)
    tx = frappe.get_doc("Imported Transaction", doc.imported_transaction)
    return frappe._dict(transaction=tx.name, amount=tx.amount, draft=doc.name,
                        journal=journal, link=link_for(doc.name))


def test_get_posted_entry_returns_one_versioned_editable_aggregate():
    aggregate = posted_fixture()
    result = posting_api.get_posted_entry(aggregate.transaction)
    assert set(result) == {"transaction", "draft", "posting", "evidence", "version"}
    assert result["transaction"]["amount"] == aggregate.amount
    assert result["draft"]["name"] == aggregate.draft
    assert result["posting"]["journal_entry"] == aggregate.journal
    assert len(result["draft"]["lines"]) == 2
    assert len(result["version"]) == 64
    assert result["draft"]["lines"][0]["account"] == account("560201")
    assert result["draft"]["lines"][0]["account_number"] == "560201"
    assert result["draft"]["lines"][0]["account_name"]
    assert result["evidence"] == {"name": None, "display_title": "", "note_html": ""}
    assert frappe.allowed_http_methods_for_whitelisted_func[posting_api.get_posted_entry] == ["GET", "POST"]


def test_viewer_can_read_posted_entry_aggregate_without_private_import_fields():
    aggregate = posted_fixture()
    frappe.set_user(user("账务查看者"))
    result = posting_api.get_posted_entry(aggregate.transaction)
    assert result["draft"]["status"] == "Posted"
    assert not {"dedupe_key", "candidate_dedupe_key", "source_id", "raw_row"} & set(result["transaction"])
    assert "owner" not in result["draft"]


def test_non_ledger_users_cannot_read_posted_entry():
    aggregate = posted_fixture()
    outsider = user("System Manager")
    for actor in (outsider, "Guest"):
        frappe.set_user(actor)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            posting_api.get_posted_entry(aggregate.transaction)


def test_read_rejects_other_company_even_for_administrator():
    aggregate = posted_fixture()
    tx = frappe.get_doc("Imported Transaction", aggregate.transaction)
    frappe.db.set_value("Import Batch", tx.import_batch, "company", "_Test Company")
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        posting_api.get_posted_entry(aggregate.transaction)


def test_read_rejects_draft_from_other_company():
    aggregate = posted_fixture()
    frappe.db.set_value("Voucher Draft", aggregate.draft, "company", "_Test Company")
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        posting_api.get_posted_entry(aggregate.transaction)


def test_read_rejects_account_from_other_company():
    aggregate = posted_fixture()
    line = frappe.get_doc("Voucher Draft", aggregate.draft).lines[0]
    frappe.db.set_value("Account", line.account, "company", "_Test Company")
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        posting_api.get_posted_entry(aggregate.transaction)


def test_read_requires_configured_company():
    aggregate = posted_fixture()
    frappe.db.set_single_value("Internal Ledger Settings", "company", None)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        posting_api.get_posted_entry(aggregate.transaction)


def test_read_rejects_missing_draft_unposted_draft_or_missing_link():
    from internal_ledger.api.review_api import approve_draft

    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "该流水尚未生成凭证"):
        posting_api.get_posted_entry(transaction(occurred_at="2026-10-02 10:00:00"))
    doc = draft()
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "只能维护已过账账目"):
        posting_api.get_posted_entry(doc.imported_transaction)
    posting_api.post_draft(approve_draft(doc.name).name)
    frappe.db.delete("Posting Link", {"name": link_for(doc.name)})
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "只能维护已过账账目"):
        posting_api.get_posted_entry(doc.imported_transaction)


def test_read_rejects_reversed_link_or_reversal_reference():
    aggregate = posted_fixture()
    for values in ({"status": "Reversed"}, {"status": "Posted", "reversal_journal_entry": aggregate.journal}):
        frappe.db.set_value("Posting Link", aggregate.link, values)
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "已冲销凭证不能编辑或删除"):
            posting_api.get_posted_entry(aggregate.transaction)


def test_evidence_metadata_is_sanitized_and_read_does_not_write():
    from internal_ledger.api.evidence_api import get_evidence, save_evidence

    aggregate = posted_fixture()
    save_evidence(aggregate.transaction, "<p>核对备注</p>",
                  get_evidence(aggregate.transaction)["note_version"], display_title=" 核对标题 ")
    evidence = frappe.db.get_value("Ledger Evidence", {"imported_transaction": aggregate.transaction}, "name")
    frappe.db.set_value("Ledger Evidence", evidence, "note_html", '<p>核对备注</p><script>alert(1)</script>')
    before = {doctype: frappe.db.count(doctype) for doctype in (
        "Ledger Evidence", "Internal Ledger Audit Event", "Journal Entry", "GL Entry")}
    modified = frappe.db.get_value("Ledger Evidence", evidence, "modified")
    frappe.set_user(user("账务查看者"))
    result = posting_api.get_posted_entry(aggregate.transaction)
    assert result["evidence"]["display_title"] == "核对标题"
    assert "<p>核对备注</p>" in result["evidence"]["note_html"]
    assert "<script>" not in result["evidence"]["note_html"]
    assert set(result["evidence"]) == {"name", "display_title", "note_html"}
    assert {doctype: frappe.db.count(doctype) for doctype in before} == before
    assert frappe.db.get_value("Ledger Evidence", evidence, "modified") == modified


def test_evidence_from_other_company_is_not_returned():
    from internal_ledger.api.evidence_api import get_evidence, save_evidence

    aggregate = posted_fixture()
    save_evidence(aggregate.transaction, "<p>private</p>",
                  get_evidence(aggregate.transaction)["note_version"], display_title="private")
    evidence = frappe.db.get_value("Ledger Evidence", {"imported_transaction": aggregate.transaction}, "name")
    frappe.db.set_value("Ledger Evidence", evidence, "company", "_Test Company")
    assert posting_api.get_posted_entry(aggregate.transaction)["evidence"] == {
        "name": None, "display_title": "", "note_html": ""}


def test_version_is_stable_and_changes_for_every_contract_input():
    from internal_ledger.accounting.posted_entry_maintenance import posted_entry_version

    aggregate = posted_fixture()
    tx = frappe.get_doc("Imported Transaction", aggregate.transaction)
    doc = frappe.get_doc("Voucher Draft", aggregate.draft)
    link = frappe.get_doc("Posting Link", aggregate.link)
    version = posted_entry_version(tx, doc, link)
    assert posting_api.get_posted_entry(tx.name)["version"] == version
    assert posting_api.get_posted_entry(tx.name)["version"] == version
    for target, field, value in (
        (tx, "modified", "2026-11-06 12:00:00"),
        (doc, "modified", "2026-11-06 12:00:00"),
        (link, "modified", "2026-11-06 12:00:00"),
        (link, "journal_entry", "changed-journal"),
        (doc.lines[0], "name", "changed-line"),
        (doc.lines[0], "account", account("560202")),
        (doc.lines[0], "debit", 13),
        (doc.lines[0], "credit", 1),
    ):
        original = target.get(field)
        target.set(field, value)
        assert posted_entry_version(tx, doc, link) != version, field
        target.set(field, original)
    doc.lines.reverse()
    assert posted_entry_version(tx, doc, link) != version


def test_version_uses_exact_sorted_json_hash_and_zero_normalization():
    from internal_ledger.accounting.posted_entry_maintenance import posted_entry_version

    tx = frappe._dict(modified="tx-time")
    doc = frappe._dict(modified="draft-time", lines=[
        frappe._dict(name="line", account="account", debit=None, credit=0)])
    link = frappe._dict(modified="posting-time", journal_entry="journal")
    expected = sha256(json.dumps({
        "transaction": "tx-time", "draft": "draft-time", "posting": "posting-time", "journal": "journal",
        "lines": [("line", "account", "0", "0")],
    }, sort_keys=True, indent=1).encode()).hexdigest()
    assert posted_entry_version(tx, doc, link) == expected
    same = deepcopy(doc)
    same.lines[0].debit = 0
    assert posted_entry_version(tx, same, link) == expected


def test_internal_loader_supports_for_update():
    from internal_ledger.accounting.posted_entry_maintenance import get_posted_entry

    aggregate = posted_fixture()
    assert get_posted_entry(aggregate.transaction, for_update=True) == posting_api.get_posted_entry(aggregate.transaction)


def tear_down():
    frappe.set_user("Administrator")
    frappe.db.rollback()
    frappe.clear_cache()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )
