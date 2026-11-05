import unittest
from decimal import Decimal

import frappe

from internal_ledger.tests.test_classifier import account, add_rule, set_up as classification_set_up, tear_down, transaction
from internal_ledger.tests.test_drafts import make_draft


def draft():
    return make_draft("expense", "expense", account("560201"), None)


def additional_draft():
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft

    name = transaction(direction="expense", amount=Decimal("12.50"), occurred_at="2026-10-02 10:00:00")
    return frappe.get_doc("Voucher Draft", create_draft(name, classify(name)))


def set_up():
    classification_set_up()
    if not frappe.db.exists("Fiscal Year", "2026"):
        frappe.get_doc({"doctype": "Fiscal Year", "year": "2026", "year_start_date": "2026-01-01",
                        "year_end_date": "2026-12-31"}).insert()


def test_viewer_cannot_trigger_auto_classification():
    from internal_ledger.api.review_api import auto_classify_pending
    from internal_ledger.tests.test_permissions import user

    try:
        frappe.set_user(user("账务查看者"))
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            auto_classify_pending()
    finally:
        frappe.set_user("Administrator")


def test_only_draft_can_be_approved():
    from internal_ledger.api.review_api import approve_draft

    doc = draft()
    assert approve_draft(doc.name).status == "Approved"
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "当前状态不可审核"):
        approve_draft(doc.name)
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_rejection_requires_reason_and_reopen_before_approval():
    from internal_ledger.api.review_api import approve_draft, reject_draft, reopen_draft

    doc = draft()
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        reject_draft(doc.name, "  ")
    rejected = reject_draft(doc.name, "请核对办公费")
    assert rejected.status == "Rejected" and rejected.rejection_reason == "请核对办公费"
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        approve_draft(doc.name)
    assert reopen_draft(doc.name).status == "Draft"
    assert approve_draft(doc.name).status == "Approved"


def test_batch_review_approves_or_rejects_all_selected_drafts():
    from internal_ledger.api.review_api import batch_review_drafts

    approved = [draft(), additional_draft()]
    outcomes = batch_review_drafts([row.name for row in approved], "approve")
    assert [row["status"] for row in outcomes] == ["Approved", "Approved"]
    assert all(frappe.get_doc("Voucher Draft", row.name).status == "Approved" for row in approved)

    rejected = [additional_draft(), additional_draft()]
    outcomes = batch_review_drafts([row.name for row in rejected], "reject", "批量核对不通过")
    assert [row["status"] for row in outcomes] == ["Rejected", "Rejected"]
    assert all(frappe.get_doc("Voucher Draft", row.name).rejection_reason == "批量核对不通过"
               for row in rejected)
    assert frappe.allowed_http_methods_for_whitelisted_func[batch_review_drafts] == ["POST"]


def test_batch_review_keeps_processing_after_one_selected_draft_fails():
    from internal_ledger.api.review_api import batch_review_drafts

    first, second = draft(), additional_draft()
    outcomes = batch_review_drafts([first.name, "missing-draft", second.name], "approve")
    assert [row["status"] for row in outcomes] == ["Approved", "failed", "Approved"]
    assert outcomes[1]["name"] == "missing-draft" and outcomes[1]["message"]


def test_manual_edit_is_controlled_and_approval_preserves_it():
    from internal_ledger.api.review_api import approve_draft, update_draft

    doc = draft()
    lines = [{"account": account("560202"), "debit": "12.50", "credit": "0"},
             {"account": account("1001"), "debit": "0", "credit": "12.50"}]
    result = update_draft(doc.name, lines, "人工核对交通费", "2026-10-02")
    assert result.is_manual_override and result.manual_override_by == "Administrator"
    assert result.lines[0].account == account("560202")
    assert str(result.posting_date) == "2026-10-02"
    approved = approve_draft(doc.name)
    assert approved.lines[0].account == account("560202")
    assert approved.summary == "人工核对交通费" and str(approved.posting_date) == "2026-10-02"
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_direct_save_cannot_forge_override_or_mutate_manual_lines():
    from internal_ledger.api.review_api import update_draft

    doc = draft()
    doc.is_manual_override = 1
    doc.manual_override_by = "Administrator"
    doc.lines[0].account = account("560202")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        doc.save()
    doc.reload()
    update_draft(doc.name, [row.as_dict() for row in doc.lines], "核对", str(doc.posting_date))
    for field, value in (("summary", "伪造"), ("posting_date", "2026-10-03"),
                         ("manual_override_by", "Guest"), ("is_manual_override", 0)):
        doc.reload()
        doc.set(field, value)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            doc.save()
    doc.reload()
    doc.lines[0].account = account("560202")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        doc.save()


def test_update_rejects_unbalanced_wrong_root_and_invalid_date():
    from internal_ledger.api.review_api import update_draft

    doc = draft()
    for lines, date in (([{"account": account("560201"), "debit": "12.49"},
                          {"account": account("1001"), "credit": "12.50"}], "2026-10-01"),
                        ([{"account": account("5001"), "debit": "12.50"},
                          {"account": account("1001"), "credit": "12.50"}], "2026-10-01"),
                        ([row.as_dict() for row in doc.lines], "not-a-date")):
        with unittest.TestCase().assertRaises((frappe.ValidationError, ValueError)):
            update_draft(doc.name, lines, "核对", date)
    doc.reload()
    assert doc.status == "Draft" and not doc.is_manual_override


def test_approval_revalidates_current_rule_and_manual_accounts():
    from internal_ledger.api.review_api import approve_draft, update_draft

    doc = draft()
    frappe.db.set_value("Classification Rule", doc.classification_rule, "enabled", 0)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        approve_draft(doc.name)
    frappe.db.set_value("Classification Rule", doc.classification_rule, "enabled", 1)
    update_draft(doc.name, [row.as_dict() for row in doc.lines], "核对", str(doc.posting_date))
    frappe.db.set_value("Account", account("560201"), "disabled", 1)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        approve_draft(doc.name)
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Draft"


def test_transition_map_and_no_posted_review_action():
    from internal_ledger.api import review_api

    assert review_api.ALLOWED_TRANSITIONS == {
        "Draft": {"Approved", "Rejected"}, "Rejected": {"Draft"}, "Approved": {"Posted"},
    }
    doc = draft()
    doc.status = "Posted"
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        doc.save()
    approved = review_api.approve_draft(doc.name)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        review_api.update_draft(doc.name, [row.as_dict() for row in approved.lines], "不能改", str(approved.posting_date))
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        review_api.reject_draft(doc.name, "不能驳回")


def test_client_flags_cannot_authorize_override_and_source_is_immutable():
    from internal_ledger.api.review_api import approve_draft, update_draft

    doc = draft()
    payload = doc.as_dict()
    payload.update(is_manual_override=1, manual_override_by="Administrator",
                   flags={"review_edit": True, "review_transition": "Approved"})
    payload["lines"][0]["account"] = account("560202")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        frappe.get_doc(payload).save()
    update_draft(doc.name, [row.as_dict() for row in doc.lines], "人工核对", str(doc.posting_date))
    other = transaction()
    manual = frappe.get_doc("Voucher Draft", doc.name)
    manual.imported_transaction = other
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        manual.save()
    approve_draft(doc.name)


def test_split_manual_lines_and_basic_period_validation():
    from internal_ledger.api.review_api import update_draft

    doc = draft()
    lines = [{"account": account("560201"), "debit": "10", "credit": "0"},
             {"account": account("560202"), "debit": "2.5", "credit": "0"},
             {"account": account("1001"), "debit": "0", "credit": "12.5"}]
    saved = update_draft(doc.name, lines, "拆分费用", "2026-10-02")
    assert len(saved.lines) == 3 and saved.is_manual_override
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        update_draft(doc.name, lines, "无会计年度", "1900-01-01")


def test_detail_includes_original_lines_and_month_counts():
    from internal_ledger.api.review_api import get_dashboard_counts, get_review_item, list_review_items

    doc = draft()
    item = get_review_item(doc.name)
    assert item["draft"]["name"] == doc.name
    assert item["transaction"]["name"] == doc.imported_transaction
    assert len(item["draft"]["lines"]) == 2 and item["can_approve"]
    counts = get_dashboard_counts("2026-10")
    assert Decimal(counts["expense"]) == Decimal("12.50") and counts["pending_review"] == 1
    assert any(row["draft"]["name"] == doc.name for row in list_review_items())


def test_posted_detail_exposes_reversal_state_and_display_title():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.api.evidence_api import get_evidence, save_evidence
    from internal_ledger.api.review_api import approve_draft, get_review_item

    doc = draft()
    approve_draft(doc.name)
    journal = post_draft(doc.name)
    current = get_evidence(doc.imported_transaction)
    save_evidence(doc.imported_transaction, "", current["note_version"], display_title="展示标题")
    item = get_review_item(doc.name)

    assert item["display_title"] == "展示标题"
    assert item["posting"]["status"] == "Posted"
    assert item["posting"]["journal_entry"] == journal
    assert not item["posting"]["reversal_journal_entry"]


def test_latest_batch_counts_follow_drafts_duplicate_resolution_and_manual_work():
    from uuid import uuid4
    from internal_ledger.api.classification_api import classify_manually
    from internal_ledger.api.duplicate_api import resolve_duplicate
    from internal_ledger.api.posting_api import approve_and_post
    from internal_ledger.api.review_api import get_dashboard_counts
    from internal_ledger.accounting.batch_workflow import process_transactions
    from internal_ledger.importing.models import save_batch
    from internal_ledger.tests.test_dedupe import tx

    draft()  # Older batches must not contribute to the latest batch summary.
    source_id = uuid4().hex
    common = {"payment_account": account("1001"), "occurred_at": "2026-10-02 10:00:00"}
    batch = save_batch("wechat", uuid4().hex, [
        tx(source_id=source_id, raw_row=2, **common),
        tx(source_id=source_id, raw_row=3, **common),
        tx(source_id=uuid4().hex, counterparty="待人工商户", raw_row=4, **common)])
    rows = frappe.get_all("Imported Transaction", filters={"import_batch": batch},
                          fields=["name"], order_by="raw_row")
    outcomes = process_transactions([row.name for row in rows])
    assert [row["status"] for row in outcomes] == ["created", "duplicate", "manual"]
    assert frappe.db.count("Imported Transaction", {"import_batch": batch}) == 3
    expected = {"batch_name": batch, "total": 2, "created": 1, "duplicate": 1, "manual": 1}
    assert get_dashboard_counts("2026-10")["latest_batch"] == expected
    assert get_dashboard_counts("1900-01")["latest_batch"] == expected
    released = resolve_duplicate(rows[1].name, "release", "独立交易")
    assert released["processing"]["status"] == "created"
    expected.update(total=3, created=2, duplicate=0)
    assert get_dashboard_counts("2026-10")["latest_batch"] == expected
    classify_manually(rows[2].name, "expense", account("560201"), account("1001"), "人工核对", 0)
    expected["manual"] = 0
    assert get_dashboard_counts("2026-10")["latest_batch"] == expected
    assert approve_and_post([outcomes[0]["draft"]])[0]["status"] == "created"
    assert get_dashboard_counts("2026-10")["latest_batch"] == expected


def test_latest_batch_is_none_without_imports():
    from unittest.mock import patch
    from internal_ledger.api import review_api

    with patch.object(review_api, "list_review_items", return_value=[]), \
            patch.object(frappe, "get_list", return_value=[]):
        assert review_api.get_dashboard_counts("2026-10")["latest_batch"] is None


def test_latest_batch_includes_an_empty_import_instead_of_an_older_draft():
    from uuid import uuid4
    from internal_ledger.api.review_api import get_dashboard_counts
    from internal_ledger.importing.models import save_batch

    draft()
    batch = save_batch("wechat", uuid4().hex, [])
    assert get_dashboard_counts("2026-10")["latest_batch"] == {
        "batch_name": batch, "total": 0, "created": 0, "duplicate": 0, "manual": 0}


def test_pending_transaction_can_be_classified_without_auto_approval():
    from internal_ledger.api.review_api import list_review_items, prepare_draft

    add_rule()
    tx_name = transaction()
    assert any(row["transaction"]["name"] == tx_name and row["draft"] is None for row in list_review_items())
    result = prepare_draft(tx_name)
    assert result.status == "Draft" and result.imported_transaction == tx_name
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_viewer_cannot_modify_and_writes_are_post_only():
    from internal_ledger.api import review_api

    doc = draft()
    user = frappe.get_doc({"doctype": "User", "email": "ledger-review-viewer@example.com",
                           "first_name": "Viewer", "send_welcome_email": 0,
                           "roles": [{"role": "账务查看者"}]}).insert()
    try:
        frappe.set_user(user.name)
        assert review_api.get_review_item(doc.name)["draft"]["name"] == doc.name
        for action, args in ((review_api.approve_draft, (doc.name,)),
                             (review_api.reject_draft, (doc.name, "驳回")),
                             (review_api.reopen_draft, (doc.name,)),
                             (review_api.update_draft, (doc.name, [], "", "2026-10-01")),
                             (review_api.prepare_draft, (doc.imported_transaction,))):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                action(*args)
            assert frappe.allowed_http_methods_for_whitelisted_func[action] == ["POST"]
    finally:
        frappe.set_user("Administrator")


def assert_foreign_child_rejected(manual):
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft
    from internal_ledger.api.review_api import approve_draft, update_draft

    source = draft()
    if manual:
        source = update_draft(source.name, [row.as_dict() for row in source.lines],
                              "人工确认", str(source.posting_date))
    else:
        source = approve_draft(source.name)
    tx_name = transaction(occurred_at="2026-10-02 10:00:00")
    target = frappe.get_doc("Voucher Draft", create_draft(tx_name, classify(tx_name)))
    source_snapshot = frappe.get_doc("Voucher Draft", source.name).as_dict()
    target_snapshot = target.as_dict()
    for local in (0, 1):
        target.reload()
        target.lines[0].name = source.lines[0].name
        target.lines[0].set("__islocal", local)
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "分录行身份"):
            target.save()
        assert frappe.get_doc("Voucher Draft", source.name).as_dict() == source_snapshot
        assert frappe.get_doc("Voucher Draft", target.name).as_dict() == target_snapshot


def test_approved_draft_child_cannot_be_moved_by_another_draft():
    assert_foreign_child_rejected(manual=False)


def test_manual_override_child_cannot_be_moved_by_another_draft():
    assert_foreign_child_rejected(manual=True)


def test_nonexistent_existing_child_name_is_rejected():
    doc = draft()
    before = doc.as_dict()
    doc.lines[0].name = "forged-existing-child-name"
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "分录行身份"):
        doc.save()
    assert frappe.get_doc("Voucher Draft", doc.name).as_dict() == before


def test_existing_child_requires_correct_database_parent_type_and_field():
    doc = draft()
    for field, invalid in (("parenttype", "Other Parent"), ("parentfield", "other_lines")):
        frappe.db.set_value("Voucher Draft Line", doc.lines[0].name, field, invalid)
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "分录行身份"):
            doc.save()
        frappe.db.set_value("Voucher Draft Line", doc.lines[0].name, field,
                            "Voucher Draft" if field == "parenttype" else "lines")
        doc.reload()


def test_update_draft_new_children_are_inserted_under_current_parent():
    from internal_ledger.api.review_api import update_draft

    doc = draft()
    old_names = {row.name for row in doc.lines}
    result = update_draft(doc.name, [row.as_dict() for row in doc.lines], "合法新行", str(doc.posting_date))
    assert old_names.isdisjoint({row.name for row in result.lines})
    for row in result.lines:
        assert frappe.db.get_value("Voucher Draft Line", row.name,
                                    ["parent", "parenttype", "parentfield"]) == (doc.name, "Voucher Draft", "lines")


def assert_duplicate_child_rejected(state, canonical_alias=False):
    from internal_ledger.api.review_api import approve_draft, update_draft

    doc = draft()
    if state == "Approved":
        approve_draft(doc.name)
    elif state == "Manual":
        update_draft(doc.name, [row.as_dict() for row in doc.lines], "人工确认", str(doc.posting_date))
    doc.reload()
    if canonical_alias:
        canonical_name = "ledger-canonical-child-" + doc.name
        frappe.db.set_value("Voucher Draft Line", doc.lines[0].name, "name", canonical_name,
                            update_modified=False)
        doc.reload()
    before = doc.as_dict()
    for local in (0, 1):
        doc.reload()
        doc.lines[1].name = doc.lines[0].name.upper() if canonical_alias else doc.lines[0].name
        doc.lines[1].set("__islocal", local)
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "分录行身份重复"):
            doc.save()
        assert frappe.get_doc("Voucher Draft", doc.name).as_dict() == before


def test_draft_rejects_duplicate_child_names():
    assert_duplicate_child_rejected("Draft")


def test_approved_rejects_duplicate_child_names():
    assert_duplicate_child_rejected("Approved")


def test_manual_override_rejects_duplicate_child_names():
    assert_duplicate_child_rejected("Manual")


def test_draft_rejects_database_equivalent_child_names():
    assert_duplicate_child_rejected("Draft", canonical_alias=True)


def test_approved_rejects_database_equivalent_child_names():
    assert_duplicate_child_rejected("Approved", canonical_alias=True)


def test_manual_override_rejects_database_equivalent_child_names():
    assert_duplicate_child_rejected("Manual", canonical_alias=True)



def test_posted_reads_preserve_lines_and_messages_without_save_validation():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.api.review_api import approve_draft, get_review_item, list_review_items, get_dashboard_counts

    doc = approve_draft(draft().name)
    journal = post_draft(doc.name)
    before = frappe.get_doc("Voucher Draft", doc.name).as_dict()
    messages = list(frappe.local.message_log or [])
    audit_count = frappe.db.count("Internal Ledger Audit Event")
    for _ in range(2):
        item = get_review_item(doc.name)
        assert item["error"] is None and not item["can_approve"]
        assert item["draft"]["status"] == "Posted"
        assert any(row["draft"] and row["draft"]["name"] == doc.name for row in list_review_items())
        assert get_dashboard_counts("2026-10")["posted"] == 1
        assert list(frappe.local.message_log or []) == messages
    assert frappe.get_doc("Voucher Draft", doc.name).as_dict() == before
    assert frappe.db.count("Internal Ledger Audit Event") == audit_count
    assert frappe.get_doc("Journal Entry", journal).docstatus == 1


def test_rule_validation_failure_remains_manually_repairable_with_server_validation():
    from internal_ledger.api.review_api import list_review_items
    from internal_ledger.api.classification_api import classify_manually
    from internal_ledger.tests.test_permissions import user

    add_rule(credit_account=account("1001"))
    name = transaction(payment_account=account("100201"))
    item = next(row for row in list_review_items() if row["transaction"]["name"] == name)
    assert item["classification"] is None and item["error"]
    assert item.get("can_classify") is True
    args = (name, "expense", account("560201"), account("100201"), "人工修复", 0)
    viewer = user("账务查看者")
    try:
        frappe.set_user(viewer)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            classify_manually(*args)
    finally:
        frappe.set_user("Administrator")
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "资金账户"):
        classify_manually(name, "expense", account("560201"), account("1001"), "错误科目", 0)
    doc = classify_manually(*args)
    assert doc.status == "Draft" and doc.is_manual_override
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
