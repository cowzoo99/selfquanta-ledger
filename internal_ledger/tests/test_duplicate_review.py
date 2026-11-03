import unittest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

import frappe

from internal_ledger.accounting.classifier import classify
from internal_ledger.importing.models import save_batch
from internal_ledger.tests.test_classifier import account, add_rule, set_up, tear_down
from internal_ledger.tests.test_dedupe import tx


def duplicate_pair():
    batch = save_batch("wechat", uuid4().hex, [tx(amount=Decimal("78.00"), payment_account=account("1001")),
                                             tx(amount=Decimal("78.00"), payment_account=account("1001"), raw_row=3)])
    return (frappe.get_doc("Imported Transaction", {"import_batch": batch, "status": status})
            for status in ("Imported", "Duplicate"))


def test_duplicate_is_pending_and_excluded():
    original, duplicate = duplicate_pair()
    assert duplicate.duplicate_review_status == "Pending"
    assert not original.duplicate_review_status
    from internal_ledger.api.duplicate_api import list_duplicates
    assert original.name not in {row["candidate"]["name"] for row in list_duplicates()}
    assert duplicate.duplicate_of == original.name
    assert duplicate.candidate_dedupe_key == "wechat:WX-1"
    assert duplicate.duplicate_match_reason
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "重复流水"):
        classify(duplicate.name)


def test_confirm_preserves_totals_and_blocks_accounting():
    from internal_ledger.api.duplicate_api import resolve_duplicate
    from internal_ledger.api.review_api import get_dashboard_counts

    original, duplicate = duplicate_pair()
    before = get_dashboard_counts("2026-10")
    result = resolve_duplicate(duplicate.name, "confirm", "  已核对  ")
    assert result["transaction"]["status"] == "Duplicate"
    assert result["transaction"]["duplicate_review_status"] == "Confirmed"
    assert result["transaction"]["duplicate_review_note"] == "已核对"
    assert result["processing"]["name"] == duplicate.name
    assert result["processing"]["status"] == "duplicate" and result["processing"]["draft"] is None
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": duplicate.name})
    assert get_dashboard_counts("2026-10") == before
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "重复流水"):
        classify(duplicate.name)
    assert frappe.db.count("Internal Ledger Audit Event", {"action": "duplicate_confirm", "reference_name": duplicate.name}) == 1
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "已经处理"):
        resolve_duplicate(duplicate.name, "release", "不能重放")


def test_release_preserves_link_and_enters_normal_flow_once():
    from internal_ledger.importing.duplicate_review import release_duplicate
    from internal_ledger.accounting.drafts import create_draft

    original, duplicate = duplicate_pair()
    add_rule()
    released = release_duplicate(duplicate.name, "Administrator", "两笔真实交易")
    assert released.status == "Imported"
    assert released.duplicate_review_status == "Released"
    assert released.duplicate_of == original.name
    assert released.dedupe_key == f"released:{duplicate.name}"
    assert released.candidate_dedupe_key == "wechat:WX-1"
    assert released.duplicate_reviewed_by == "Administrator"
    assert released.duplicate_reviewed_at
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": released.name})
    assert create_draft(released.name, classify(released.name))
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "已经处理"):
        release_duplicate(duplicate.name, "Administrator", "重试")
    assert frappe.db.count("Internal Ledger Audit Event", {"action": "duplicate_release", "reference_name": duplicate.name}) == 1
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_release_duplicate_immediately_reprocesses_it():
    from internal_ledger.api.duplicate_api import resolve_duplicate
    from internal_ledger.accounting.batch_workflow import process_transactions

    add_rule()
    _, duplicate = duplicate_pair()
    result = resolve_duplicate(duplicate.name, "release", "两笔真实交易")
    assert result["transaction"]["duplicate_review_status"] == "Released"
    assert result["processing"]["status"] == "created"
    assert result["processing"]["draft"]
    draft = frappe.get_doc("Voucher Draft", result["processing"]["draft"])
    assert draft.imported_transaction == duplicate.name and draft.status == "Draft"
    assert process_transactions([duplicate.name])[0]["draft"] == draft.name
    assert frappe.db.count("Voucher Draft", {"imported_transaction": duplicate.name}) == 1
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_release_without_a_rule_returns_manual_work():
    from internal_ledger.api.duplicate_api import resolve_duplicate

    _, duplicate = duplicate_pair()
    result = resolve_duplicate(duplicate.name, "release", "两笔真实交易")
    assert result["transaction"]["duplicate_review_status"] == "Released"
    assert result["processing"]["status"] == "manual" and result["processing"]["draft"] is None
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": duplicate.name})


def test_invalid_decision_and_actor_leave_candidate_unchanged():
    from internal_ledger.importing.duplicate_review import resolve

    original, duplicate = duplicate_pair()
    before = duplicate.as_dict()
    for decision, actor, error in (("invalid", "Administrator", frappe.ValidationError),
                                   ("release", "Guest", frappe.PermissionError)):
        with unittest.TestCase().assertRaises(error):
            resolve(duplicate.name, decision, actor, "说明")
        assert frappe.get_doc("Imported Transaction", duplicate.name).as_dict() == before


def test_list_evidence_and_viewer_cannot_resolve_or_see_sensitive_keys():
    from internal_ledger.api.duplicate_api import list_duplicates, resolve_duplicate
    from internal_ledger.importing.duplicate_review import release_duplicate
    from internal_ledger.tests.test_permissions import user

    original, duplicate = duplicate_pair()
    item = next(row for row in list_duplicates() if row["candidate"]["name"] == duplicate.name)
    assert item["original"]["name"] == original.name
    assert item["candidate"]["candidate_dedupe_key"] == "wechat:WX-1"
    resolve_duplicate(duplicate.name, "release", "两笔真实交易")
    viewer = user("账务查看者")
    try:
        frappe.set_user(viewer)
        item = next(row for row in list_duplicates() if row["candidate"]["name"] == duplicate.name)
        for row in (item["candidate"], item["original"]):
            assert not {"source_id", "dedupe_key", "candidate_dedupe_key"} & row.keys()
        from internal_ledger.api.review_api import list_review_items
        reviewed = next(row for row in list_review_items() if row["transaction"]["name"] == duplicate.name)
        assert "candidate_dedupe_key" not in reviewed["transaction"]
        for operation in (lambda: resolve_duplicate(duplicate.name, "confirm", "说明"),
                          lambda: release_duplicate(duplicate.name, viewer, "说明")):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
    finally:
        frappe.set_user("Administrator")
    assert frappe.allowed_http_methods_for_whitelisted_func[resolve_duplicate] == ["POST"]


def test_normal_import_validation_remains_strict():
    from internal_ledger.permissions import _IMPORT_WRITE

    original, duplicate = duplicate_pair()
    for status, review, key, link in (("Duplicate", "Released", None, original.name),
                                       ("Duplicate", "Pending", "active-key", original.name),
                                       ("Imported", None, "active-key", original.name),
                                       ("Imported", "Released", "wrong-key", original.name),
                                       ("Imported", "Released", f"released:{duplicate.name}", None)):
        duplicate.reload()
        duplicate.status, duplicate.duplicate_review_status = status, review
        duplicate.dedupe_key, duplicate.duplicate_of = key, link
        duplicate.flags.ledger_import_write = _IMPORT_WRITE
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            duplicate.save()


def test_audit_failure_rolls_back_resolution():
    from internal_ledger.importing.duplicate_review import resolve

    original, duplicate = duplicate_pair()
    before = duplicate.as_dict()
    with patch("internal_ledger.importing.duplicate_review.record_audit", side_effect=RuntimeError("audit unavailable")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "audit unavailable"):
            resolve(duplicate.name, "release", "Administrator", "说明")
    assert frappe.get_doc("Imported Transaction", duplicate.name).as_dict() == before


def test_company_permissions_apply_to_candidate_and_original():
    from internal_ledger.api.duplicate_api import list_duplicates, resolve_duplicate
    from internal_ledger.tests.test_permissions import user

    original, duplicate = duplicate_pair()
    admin = user("账套管理员")
    # Existing fixture company becomes foreign when settings switch to another company.
    frappe.db.set_single_value("Internal Ledger Settings", "company", "")
    try:
        frappe.set_user(admin)
        assert not list_duplicates()
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            resolve_duplicate(duplicate.name, "release", "说明")
    finally:
        frappe.set_user("Administrator")


def test_migration_backfills_only_legacy_duplicates_idempotently():
    from internal_ledger.patches.backfill_duplicate_review import execute

    original, duplicate = duplicate_pair()
    original_before = original.as_dict()
    for field in ("duplicate_review_status", "candidate_dedupe_key", "duplicate_match_reason"):
        frappe.db.set_value("Imported Transaction", duplicate.name, field, None, update_modified=False)
    execute()
    migrated = frappe.get_doc("Imported Transaction", duplicate.name)
    assert migrated.duplicate_review_status == "Pending"
    assert migrated.candidate_dedupe_key == "wechat:WX-1"
    assert migrated.duplicate_of == original.name
    assert not migrated.dedupe_key
    before = migrated.as_dict()
    execute()
    assert frappe.get_doc("Imported Transaction", duplicate.name).as_dict() == before
    assert frappe.get_doc("Imported Transaction", original.name).as_dict() == original_before


def test_release_persists_stable_manual_and_conflict_processing_audit():
    from internal_ledger.api.duplicate_api import resolve_duplicate

    for expected in ("manual", "conflict"):
        if expected == "conflict":
            add_rule()
            add_rule(debit_account=account("560202"))
        source_id = uuid4().hex
        batch = save_batch("wechat", uuid4().hex, [
            tx(source_id=source_id, payment_account=account("1001")),
            tx(source_id=source_id, payment_account=account("1001"), raw_row=3)])
        duplicate = frappe.get_doc("Imported Transaction", {"import_batch": batch, "status": "Duplicate"})
        result = resolve_duplicate(duplicate.name, "release", "两笔独立交易")
        assert result["processing"]["status"] == expected
        events = frappe.get_all("Internal Ledger Audit Event", filters={
            "action": "duplicate_release_processing", "reference_name": duplicate.name}, fields=["*"])
        assert len(events) == 1
        event = events[0]
        assert (event.reference_doctype, event.result, event.actor, event.company) == (
            "Imported Transaction", expected, "Administrator", frappe.flags.classification_company)
        assert event.timestamp
        assert duplicate.summary not in str(event)
        assert "未匹配" not in str(event) and "冲突" not in str(event)


def test_release_processing_audit_failure_rolls_back_release_and_draft():
    from internal_ledger.api.duplicate_api import resolve_duplicate

    add_rule()
    _, duplicate = duplicate_pair()
    before = duplicate.as_dict()
    audit_count = frappe.db.count("Internal Ledger Audit Event")
    with patch("internal_ledger.api.duplicate_api.record_audit", create=True,
               side_effect=RuntimeError("audit unavailable")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "audit unavailable"):
            resolve_duplicate(duplicate.name, "release", "独立交易")
    assert frappe.get_doc("Imported Transaction", duplicate.name).as_dict() == before
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": duplicate.name})
    assert frappe.db.count("Internal Ledger Audit Event") == audit_count


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
