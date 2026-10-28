import unittest
from decimal import Decimal

import frappe

from internal_ledger.tests.test_classifier import account, add_rule, set_up as classification_set_up, tear_down, transaction
from internal_ledger.tests.test_drafts import make_draft


def draft():
    return make_draft("expense", "expense", account("560201"), None)


def set_up():
    classification_set_up()
    if not frappe.db.exists("Fiscal Year", "2026"):
        frappe.get_doc({"doctype": "Fiscal Year", "year": "2026", "year_start_date": "2026-01-01",
                        "year_end_date": "2026-12-31"}).insert()


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
    tx_name = transaction()
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


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
