import json
import unittest
from copy import deepcopy
from hashlib import sha256
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

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



def changed_values(old):
    return {
        "occurred_at": "2026-08-16 09:30:00", "posting_date": "2026-08-16",
        "counterparty": "新交易对象", "summary": "调整后的办公采购", "direction": "expense",
        "transaction_type": "expense", "amount": "125.00", "payment_account": account("1001"),
        "lines": [
            {"account": account("560201"), "debit": "125.00", "credit": "0"},
            {"account": account("1001"), "debit": "0", "credit": "125.00"},
        ],
    }


def posting_snapshot(old):
    return {
        "aggregate": posting_api.get_posted_entry(old.transaction),
        "transaction": frappe.get_doc("Imported Transaction", old.transaction).as_dict(),
        "draft": frappe.get_doc("Voucher Draft", old.draft).as_dict(),
        "journal": frappe.get_doc("Journal Entry", old.journal).as_dict(),
        "gl": frappe.get_all("GL Entry", filters={"voucher_no": old.journal}, fields=["*"], order_by="name"),
        "series": frappe.db.sql("SELECT name, current FROM `tabSeries` ORDER BY name"),
        "counts": {doctype: frappe.db.count(doctype) for doctype in (
            "Journal Entry", "GL Entry", "Posting Link", "Internal Ledger Audit Event", "Ledger Evidence")},
    }


def assert_rebuild_rejected(old, values, message=None, version=None):
    before = posting_snapshot(old)
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, message or "."):
        posting_api.rebuild_posted_entry(old.transaction, values, version or before["aggregate"]["version"])
    assert posting_snapshot(old) == before


def test_rebuild_updates_transaction_draft_new_journal_and_general_ledger():
    old = posted_fixture()
    before = posting_api.get_posted_entry(old.transaction)
    result = posting_api.rebuild_posted_entry(old.transaction, changed_values(old), before["version"])
    journal_name = result["posting"]["journal_entry"]
    assert journal_name != old.journal
    assert int(journal_name.rsplit("-", 1)[1]) == int(old.journal.rsplit("-", 1)[1]) + 1
    assert not frappe.db.exists("Journal Entry", old.journal)
    assert not frappe.db.count("GL Entry", {"voucher_no": old.journal})
    assert not frappe.db.exists("Posting Link", old.link)
    assert result["version"] != before["version"]
    tx = frappe.get_doc("Imported Transaction", old.transaction)
    assert Decimal(str(tx.amount)) == Decimal("125.00")
    assert str(tx.occurred_at) == "2026-08-16 09:30:00"
    assert (tx.counterparty, tx.summary, tx.direction, tx.payment_account) == (
        "新交易对象", "调整后的办公采购", "expense", account("1001"))
    doc = frappe.get_doc("Voucher Draft", old.draft)
    assert (doc.summary, doc.status, doc.transaction_type) == ("调整后的办公采购", "Posted", "expense")
    assert doc.is_manual_override and doc.manual_override_by == "Administrator"
    assert not doc.classification_rule
    journal = frappe.get_doc("Journal Entry", journal_name)
    assert journal.docstatus == 1 and str(journal.posting_date) == "2026-08-16"
    assert journal.user_remark == doc.summary
    gl = frappe.get_all("GL Entry", filters={"voucher_no": journal_name}, fields=["debit", "credit", "is_cancelled"])
    assert len(gl) == 2 and all(not row.is_cancelled for row in gl)
    assert sum(Decimal(str(row.debit)) for row in gl) == Decimal("125")
    assert sum(Decimal(str(row.credit)) for row in gl) == Decimal("125")
    assert frappe.db.count("Posting Link", {"voucher_draft": old.draft}) == 1
    assert frappe.db.exists("Internal Ledger Audit Event", {"action": "rebuild_posted_entry",
        "reference_name": old.draft, "result": f"old:{old.journal};new:{journal_name}"})
    assert frappe.allowed_http_methods_for_whitelisted_func[posting_api.rebuild_posted_entry] == ["POST"]


def test_rebuild_rolls_back_every_layer_when_reposting_fails():
    old = posted_fixture()
    before = posting_snapshot(old)
    with patch("internal_ledger.accounting.posted_entry_maintenance.post_draft",
               side_effect=frappe.ValidationError("模拟重新过账失败")):
        assert_rebuild_rejected(old, changed_values(old), "模拟重新过账失败")
    assert posting_snapshot(old) == before


def test_rebuild_rolls_back_after_new_journal_and_gl_were_created():
    old = posted_fixture()
    with patch("internal_ledger.accounting.posted_entry_maintenance.record_audit",
               side_effect=frappe.ValidationError("模拟重建审计失败")):
        assert_rebuild_rejected(old, changed_values(old), "模拟重建审计失败")


def test_viewer_and_non_ledger_users_cannot_rebuild_posted_entry():
    from internal_ledger.accounting.posted_entry_maintenance import rebuild_posted_entry

    old = posted_fixture()
    before = posting_snapshot(old)
    actors = [user("账务查看者"), user("System Manager"), "Guest"]
    values = changed_values(old)
    for actor in actors:
        frappe.set_user(actor)
        for endpoint in (posting_api.rebuild_posted_entry, rebuild_posted_entry):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                endpoint(old.transaction, values, before["aggregate"]["version"])
        frappe.set_user("Administrator")
        assert posting_snapshot(old) == before


def test_ledger_admin_can_rebuild_json_payload():
    old = posted_fixture()
    before = posting_api.get_posted_entry(old.transaction)
    values = changed_values(old)
    actor = user("账套管理员")
    frappe.set_user(actor)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        frappe.get_doc("Journal Entry", old.journal).cancel()
    result = posting_api.rebuild_posted_entry(old.transaction, json.dumps(values), before["version"])
    assert result["draft"]["status"] == "Posted"
    assert frappe.db.get_value("Voucher Draft", old.draft, "manual_override_by") == actor


def test_rebuild_rejects_stale_version_without_changes():
    old = posted_fixture()
    assert_rebuild_rejected(old, changed_values(old), "账目已被其他人修改，请刷新后重试", "stale")


def test_rebuild_rejects_unbalanced_lines_without_changes():
    old = posted_fixture()
    values = changed_values(old)
    values["lines"][1]["credit"] = "124"
    assert_rebuild_rejected(old, values, "借贷不平")


def test_rebuild_rejects_amount_mismatch_without_changes():
    old = posted_fixture()
    values = changed_values(old)
    values["amount"] = "126"
    assert_rebuild_rejected(old, values, "借贷金额必须等于流水金额")


def test_rebuild_rejects_frozen_account_without_changes():
    old = posted_fixture()
    frappe.db.set_value("Account", account("560201"), "freeze_account", "Yes")
    frappe.clear_document_cache("Account", account("560201"))
    assert_rebuild_rejected(old, changed_values(old), "冻结")


def test_rebuild_rejects_closed_period_without_changes():
    old = posted_fixture()
    frappe.get_doc({"doctype": "Accounting Period", "period_name": "rebuild-" + uuid4().hex,
        "company": frappe.flags.classification_company, "start_date": "2026-08-16", "end_date": "2026-08-16",
        "exempted_role": "System Manager",
        "closed_documents": [{"document_type": "Journal Entry", "closed": 1}]}).insert()
    assert_rebuild_rejected(old, changed_values(old), "关闭会计期间")


def test_rebuild_rejects_dedupe_collision_without_changes():
    old = posted_fixture()
    frappe.db.set_value("Imported Transaction", old.transaction, "source_id", None)
    transaction(source_id=None, occurred_at="2026-08-16 09:30:00", amount=Decimal("125.00"),
                counterparty="新交易对象", summary="调整后的办公采购")
    assert_rebuild_rejected(old, changed_values(old), "重复")


def test_rebuild_recomputes_fingerprint_and_clears_obsolete_duplicate_review():
    old = posted_fixture()
    frappe.db.set_value("Imported Transaction", old.transaction, {
        "source_id": None, "candidate_dedupe_key": "obsolete", "duplicate_review_status": "Released",
        "duplicate_of": old.transaction, "dedupe_key": "released:" + old.transaction,
        "duplicate_match_reason": "obsolete", "duplicate_reviewed_by": "Administrator",
        "duplicate_reviewed_at": "2026-08-01 00:00:00", "duplicate_review_note": "obsolete"})
    before = posting_api.get_posted_entry(old.transaction)
    posting_api.rebuild_posted_entry(old.transaction, changed_values(old), before["version"])
    tx = frappe.get_doc("Imported Transaction", old.transaction)
    raw = "|".join((account("1001"), "2026-08-16T09:30:00", "125.00", "新交易对象", "调整后的办公采购"))
    assert tx.dedupe_key == sha256(raw.encode()).hexdigest()
    assert all(not tx.get(field) for field in (
        "duplicate_of", "candidate_dedupe_key", "duplicate_review_status", "duplicate_match_reason",
        "duplicate_reviewed_by", "duplicate_reviewed_at", "duplicate_review_note"))


def test_rebuild_rejects_invalid_input_without_changes():
    old = posted_fixture()
    good = changed_values(old)
    invalid = [None, {}, {**good, "extra": "field"}]
    invalid.extend({**good, "amount": value} for value in ("0", "-1", "NaN", "Infinity", "bad"))
    invalid.extend({**good, field: value} for field, value in (
        ("direction", "other"), ("lines", None), ("lines", [{"account": account("1001"), "debit": "bad"}]),
        ("occurred_at", None), ("posting_date", None), ("summary", [])))
    for values in invalid:
        assert_rebuild_rejected(old, values)


def test_rebuild_rejects_wrong_account_or_currency_without_changes():
    old = posted_fixture()
    values = changed_values(old)
    values["lines"][1]["account"] = account("560202")
    assert_rebuild_rejected(old, values)
    frappe.db.set_value("Account", account("560201"), "account_currency", "USD")
    frappe.clear_document_cache("Account", account("560201"))
    assert_rebuild_rejected(old, changed_values(old), "本位币")


def test_posted_draft_cannot_forge_rebuild_marker():
    old = posted_fixture()
    before = posting_snapshot(old)
    for marker in (True, "posted_entry_rebuild", {}):
        doc = frappe.get_doc("Voucher Draft", old.draft)
        doc.flags.posted_entry_rebuild = marker
        doc.summary = "unauthorized edit"
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            doc.save()
    assert posting_snapshot(old) == before



def test_rebuild_preserves_evidence_and_creates_journal_with_existing_title_and_note():
    from internal_ledger.api.evidence_api import get_evidence, save_evidence

    old = posted_fixture()
    save_evidence(old.transaction, "<p>核对备注</p>",
                  get_evidence(old.transaction)["note_version"], display_title="核对标题")
    evidence_name = frappe.db.get_value("Ledger Evidence", {"imported_transaction": old.transaction}, "name")
    evidence_before = frappe.get_doc("Ledger Evidence", evidence_name).as_dict()
    before = posting_api.get_posted_entry(old.transaction)
    result = posting_api.rebuild_posted_entry(old.transaction, changed_values(old), before["version"])
    assert result["draft"]["summary"] == "调整后的办公采购"
    journal = frappe.get_doc("Journal Entry", result["posting"]["journal_entry"])
    assert journal.name != old.journal
    assert journal.user_remark == journal.remark == "核对标题；核对备注"
    assert {row.remarks for row in frappe.get_all("GL Entry", filters={"voucher_no": journal.name},
                                                fields=["remarks"])} == {"核对标题；核对备注"}
    assert frappe.get_doc("Ledger Evidence", evidence_name).as_dict() == evidence_before


def tear_down():
    frappe.set_user("Administrator")
    frappe.db.rollback()
    frappe.clear_cache()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )

def delete_item(item, reason="录入错误", version=None):
    current = posting_api.get_posted_entry(item.transaction)
    return posting_api.permanently_delete_transactions(
        [item.transaction], reason, {item.transaction: version or current["version"]})[0]


def evidence_fixture(item):
    from internal_ledger.tests.test_evidence_api import evidence, attachment
    from pathlib import Path

    doc = evidence(item.transaction, note_html="private evidence content")
    file = attachment(doc.name, marked=True)
    return frappe._dict(evidence=doc.name, file=file.name, path=Path(file.get_full_path()))


def source_fixture(item):
    from pathlib import Path
    from internal_ledger.permissions import _SOURCE_INSERT, _IMPORT_WRITE

    batch = frappe.get_doc("Import Batch", frappe.db.get_value("Imported Transaction", item.transaction, "import_batch"))
    source = frappe.get_doc({"doctype": "Internal Ledger Source File", "import_batch": batch.name})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    file = frappe.get_doc({"doctype": "File", "file_name": "source.csv", "content": uuid4().hex,
        "attached_to_doctype": source.doctype, "attached_to_name": source.name, "is_private": 1})
    file.flags.source_insert = _SOURCE_INSERT
    file.insert()
    source.raw_file = file.file_url
    source.save()
    batch.flags.ledger_import_write = _IMPORT_WRITE
    batch.flags.source_insert = _SOURCE_INSERT
    batch.raw_file = source.name
    batch.save()
    return frappe._dict(batch=batch.name, carrier=source.name, file=file.name, path=Path(file.get_full_path()))


def test_permanent_delete_removes_all_layers_and_keeps_minimal_audit():
    item = posted_fixture()
    evidence = evidence_fixture(item)
    source = source_fixture(item)
    lines = [row.name for row in frappe.get_doc("Voucher Draft", item.draft).lines]
    assert delete_item(item) == {"transaction": item.transaction, "status": "deleted"}
    targets = (("Imported Transaction", item.transaction), ("Voucher Draft", item.draft),
        ("Journal Entry", item.journal), ("Posting Link", item.link), ("Ledger Evidence", evidence.evidence),
        ("File", evidence.file), ("File", source.file), ("Internal Ledger Source File", source.carrier),
        ("Import Batch", source.batch))
    for doctype, name in targets:
        assert not frappe.db.exists(doctype, name), doctype
        assert not frappe.db.exists("Deleted Document", {"deleted_doctype": doctype, "deleted_name": name}), doctype
    assert not frappe.db.exists("Voucher Draft Line", {"name": ["in", lines]})
    assert not frappe.db.exists("GL Entry", {"voucher_no": item.journal})
    assert not evidence.path.exists() and not source.path.exists()
    audit = frappe.get_doc("Internal Ledger Audit Event", {"action": "permanently_delete_transaction",
        "reference_name": item.transaction})
    assert (audit.company, audit.actor, audit.result) == (frappe.flags.classification_company, "Administrator", "success")
    assert "private evidence content" not in frappe.as_json(audit.as_dict())
    assert frappe.allowed_http_methods_for_whitelisted_func[posting_api.permanently_delete_transactions] == ["POST"]


def test_permanent_delete_keeps_source_until_last_batch_row():
    first, second = posted_fixture(), second_posted_fixture()
    source = source_fixture(first)
    frappe.db.set_value("Imported Transaction", second.transaction, "import_batch", source.batch)
    assert delete_item(first)["status"] == "deleted"
    assert frappe.db.exists("Internal Ledger Source File", source.carrier)
    assert frappe.db.exists("File", source.file) and source.path.exists()
    assert delete_item(second)["status"] == "deleted"
    assert not frappe.db.exists("Internal Ledger Source File", source.carrier)
    assert not frappe.db.exists("File", source.file) and not source.path.exists()
    assert not frappe.db.exists("Import Batch", source.batch)


def test_permanent_delete_releases_duplicate_candidates():
    item = posted_fixture()
    candidate = transaction()
    frappe.db.set_value("Imported Transaction", candidate, {"status": "Duplicate", "duplicate_of": item.transaction,
        "dedupe_key": None, "candidate_dedupe_key": "old", "duplicate_review_status": "Pending",
        "duplicate_match_reason": "old", "duplicate_review_note": "old"})
    assert delete_item(item)["status"] == "deleted"
    tx = frappe.get_doc("Imported Transaction", candidate)
    assert tx.status == "Imported" and tx.dedupe_key == "released:" + candidate
    assert all(not tx.get(field) for field in ("duplicate_of", "candidate_dedupe_key", "duplicate_review_status",
        "duplicate_match_reason", "duplicate_reviewed_by", "duplicate_reviewed_at", "duplicate_review_note"))


def test_permanent_delete_rejects_reason_and_stale_version_without_changes():
    item = posted_fixture()
    before = posting_snapshot(item)
    for reason, version, message in ((" ", None, "永久删除必须填写原因"),
                                     ("错误", "stale", "账目已被其他人修改，请刷新后重试")):
        result = delete_item(item, reason, version)
        assert result == {"transaction": item.transaction, "status": "failed", "message": message}
        assert posting_snapshot(item) == before


def test_permanent_delete_denies_viewer_outsider_and_guest():
    from internal_ledger.accounting import posted_entry_maintenance as service

    item = posted_fixture()
    version = posting_api.get_posted_entry(item.transaction)["version"]
    for actor in (user("账务查看者"), user("System Manager"), "Guest"):
        frappe.set_user(actor)
        for endpoint in (posting_api.permanently_delete_transactions, service.permanently_delete_transactions):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                endpoint([item.transaction], "错误", {item.transaction: version})
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            service.permanently_delete_transaction(item.transaction, "错误", version)
    frappe.set_user("Administrator")
    assert frappe.db.exists("Imported Transaction", item.transaction)


def test_permanent_delete_admin_accepts_json_and_deduplicates_in_order():
    first, second = posted_fixture(), second_posted_fixture()
    versions = {item.transaction: posting_api.get_posted_entry(item.transaction)["version"] for item in (first, second)}
    frappe.set_user(user("账套管理员"))
    result = posting_api.permanently_delete_transactions(
        json.dumps([second.transaction, first.transaction, second.transaction]), "错误", json.dumps(versions))
    assert result == [{"transaction": item.transaction, "status": "deleted"} for item in (second, first)]


def test_permanent_delete_file_failure_restores_rows_and_already_deleted_blobs():
    from internal_ledger.permissions import ProtectedLedgerFile

    item = posted_fixture()
    evidence, source = evidence_fixture(item), source_fixture(item)
    original_bytes = evidence.path.read_bytes()
    before = posting_snapshot(item)
    original = ProtectedLedgerFile.on_trash
    def fail_source(doc):
        if doc.name == source.file:
            raise OSError("模拟文件删除失败")
        return original(doc)
    with patch.object(ProtectedLedgerFile, "on_trash", fail_source):
        result = delete_item(item)
    assert result["status"] == "failed"
    assert posting_snapshot(item) == before
    assert evidence.path.read_bytes() == original_bytes and source.path.exists()
    for doctype, name in (("Ledger Evidence", evidence.evidence), ("File", evidence.file),
                           ("Internal Ledger Source File", source.carrier), ("File", source.file)):
        assert frappe.db.exists(doctype, name)


def test_permanent_delete_audit_failure_rolls_back_direct_service_and_files():
    from internal_ledger.accounting.posted_entry_maintenance import permanently_delete_transaction

    item = posted_fixture()
    evidence = evidence_fixture(item)
    before = posting_snapshot(item)
    original_bytes = evidence.path.read_bytes()
    with patch("internal_ledger.accounting.posted_entry_maintenance.record_deleted_audit",
               side_effect=frappe.ValidationError("模拟审计失败")):
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "模拟审计失败"):
            permanently_delete_transaction(item.transaction, "错误", before["aggregate"]["version"])
    assert posting_snapshot(item) == before
    assert evidence.path.read_bytes() == original_bytes


def test_permanent_delete_batch_isolates_failure_and_rejects_invalid_inputs():
    first, second = posted_fixture(), second_posted_fixture()
    versions = {item.transaction: posting_api.get_posted_entry(item.transaction)["version"] for item in (first, second)}
    versions[first.transaction] = "stale"
    result = posting_api.permanently_delete_transactions([first.transaction, second.transaction], "错误", versions)
    assert [row["status"] for row in result] == ["failed", "deleted"]
    assert frappe.db.exists("Imported Transaction", first.transaction)
    assert not frappe.db.exists("Imported Transaction", second.transaction)
    for names, versions in ((None, {}), ([], {}), ([123], {}), ([first.transaction], [])):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            posting_api.permanently_delete_transactions(names, "错误", versions)


def test_permanent_delete_rejects_foreign_company_and_reversed_posting():
    item = posted_fixture()
    version = posting_api.get_posted_entry(item.transaction)["version"]
    frappe.db.set_value("Posting Link", item.link, "reversal_journal_entry", item.journal)
    assert posting_api.permanently_delete_transactions([item.transaction], "错误", {item.transaction: version})[0]["status"] == "failed"
    frappe.db.set_value("Posting Link", item.link, "reversal_journal_entry", None)
    batch = frappe.db.get_value("Imported Transaction", item.transaction, "import_batch")
    frappe.db.set_value("Import Batch", batch, "company", "_Test Company")
    assert posting_api.permanently_delete_transactions([item.transaction], "错误", {item.transaction: version})[0]["status"] == "failed"
    assert frappe.db.exists("Journal Entry", item.journal)


def test_permanent_delete_propagates_deadlocks():
    item = posted_fixture()
    version = posting_api.get_posted_entry(item.transaction)["version"]
    with patch("internal_ledger.accounting.posted_entry_maintenance._locked_documents", side_effect=frappe.QueryDeadlockError):
        with unittest.TestCase().assertRaises(frappe.QueryDeadlockError):
            posting_api.permanently_delete_transactions([item.transaction], "错误", {item.transaction: version})


def test_permanent_delete_source_markers_cannot_be_forged():
    item = posted_fixture()
    source = source_fixture(item)
    for doctype, name in (("File", source.file), ("Internal Ledger Source File", source.carrier)):
        for marker in (True, "source_delete", {}):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                frappe.delete_doc(doctype, name, flags={"source_delete": marker}, ignore_permissions=True)
    assert source.path.exists()


def second_posted_fixture():
    from internal_ledger.tests.test_review_api import additional_draft
    from internal_ledger.api.review_api import approve_draft

    doc = approve_draft(additional_draft().name)
    journal = posting_api.post_draft(doc.name)
    return frappe._dict(transaction=doc.imported_transaction, draft=doc.name,
                        journal=journal, link=link_for(doc.name))
