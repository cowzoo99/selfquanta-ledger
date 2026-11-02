import inspect
import unittest
from decimal import Decimal
from unittest.mock import patch

import frappe

from internal_ledger.tests.test_classifier import account, add_rule, tear_down, transaction
from internal_ledger.tests.test_duplicate_review import duplicate_pair
from internal_ledger.tests.test_review_api import set_up


def classify_manual(name, remember=True, **values):
    from internal_ledger.accounting.manual_classification import classify_manually

    tx = frappe.get_doc("Imported Transaction", name)
    fields = dict(transaction_type="expense", debit_account=account("560201"),
                  credit_account=tx.payment_account, summary="软件开发服务费", remember_counterparty=remember)
    fields.update(values)
    return classify_manually(name, **fields)


def test_balanced_expense_and_exact_rule_preserve_source():
    name = transaction(counterparty="重庆八戒软件有限公司", amount=Decimal("36000"))
    before = frappe.get_doc("Imported Transaction", name).as_dict()
    doc = classify_manual(name)
    assert doc.status == "Draft" and doc.summary == "软件开发服务费"
    assert [(row.account, row.debit, row.credit) for row in doc.lines] == [
        (account("560201"), 36000, 0), (account("1001"), 0, 36000)]
    rule = frappe.get_doc("Classification Rule", doc.classification_rule)
    assert (rule.kind, rule.value, rule.direction, rule.transaction_type) == (
        "counterparty", "重庆八戒软件有限公司", "expense", "expense")
    assert frappe.get_doc("Imported Transaction", name).as_dict() == before
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals
    from internal_ledger.accounting.classifier import classify
    assert classify(transaction(counterparty="重庆八戒软件有限公司")).rule_name == rule.name


def test_identical_rule_and_repeated_call_do_not_duplicate():
    existing = add_rule()
    name = transaction()
    first = classify_manual(name)
    second = classify_manual(name)
    assert first.name == second.name
    assert first.classification_rule == existing.name
    assert frappe.db.count("Classification Rule", {"company": frappe.flags.classification_company}) == 1
    assert frappe.db.count("Voucher Draft", {"imported_transaction": name}) == 1
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        classify_manual(name, debit_account=account("560202"))


def test_conflicting_exact_rules_are_rejected_without_mutation():
    name = transaction()
    for fields in ({"debit_account": account("560202")},
                   {"transaction_type": "fee"},
                   {"direction": "income", "transaction_type": "income", "debit_account": account("1001"),
                    "credit_account": account("5001")}):
        rule = add_rule(**fields)
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "冲突"):
            classify_manual(name)
        assert not frappe.db.exists("Voucher Draft", {"imported_transaction": name})
        frappe.delete_doc("Classification Rule", rule.name)


def test_blank_counterparty_cannot_be_learned():
    for counterparty in ("", "   "):
        name = transaction(counterparty=counterparty)
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "交易对象"):
            classify_manual(name)
        assert not frappe.db.exists("Voucher Draft", {"imported_transaction": name})
    assert not frappe.db.count("Classification Rule", {"company": frappe.flags.classification_company})


def test_unremembered_classification_does_not_match_future_transactions():
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.api.review_api import approve_draft

    name = transaction(counterparty="本次商户")
    first = classify_manual(name, remember=False)
    assert first.status == "Draft"
    assert not frappe.db.count("Classification Rule", {"company": frappe.flags.classification_company})
    assert classify(transaction(counterparty="本次商户")).status == "unmatched"
    assert classify_manual(name, remember=False).name == first.name
    assert approve_draft(first.name).status == "Approved"


def test_blank_counterparty_allowed_without_learning():
    assert classify_manual(transaction(counterparty=""), remember=False).status == "Draft"


def test_pending_and_confirmed_duplicates_are_rejected():
    from internal_ledger.importing.duplicate_review import resolve

    original, duplicate = duplicate_pair()
    for confirmed in (False, True):
        if confirmed:
            resolve(duplicate.name, "confirm", "Administrator", "同一交易")
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "重复流水"):
            classify_manual(duplicate.name)
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": duplicate.name})


def test_released_duplicate_can_be_classified():
    from internal_ledger.importing.duplicate_review import release_duplicate

    original, duplicate = duplicate_pair()
    release_duplicate(duplicate.name, "Administrator", "两笔真实交易")
    doc = classify_manual(duplicate.name)
    assert doc.imported_transaction == duplicate.name and doc.status == "Draft"
    assert frappe.get_doc("Imported Transaction", duplicate.name).duplicate_of == original.name


def test_invalid_accounts_rejected_before_learning():
    name = transaction()
    group = frappe.db.get_value("Account", {"company": frappe.flags.classification_company, "is_group": 1}, "name")
    disabled = account("560202")
    frappe.db.set_value("Account", disabled, "disabled", 1)
    foreign = frappe.db.get_value("Account", {"company": ["!=", frappe.flags.classification_company], "is_group": 0}, "name")
    if not foreign:
        foreign = account("5603")
        frappe.db.set_value("Account", foreign, "company", "_Test Company")
    for debit in ("missing-account", group, disabled, foreign, account("1001")):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            classify_manual(name, debit_account=debit)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        classify_manual(name, transaction_type="income")
    assert not frappe.db.count("Classification Rule", {"company": frappe.flags.classification_company})


def test_viewers_read_accounts_but_cannot_classify():
    from internal_ledger.api import classification_api
    from internal_ledger.tests.test_permissions import user

    name = transaction()
    viewer = user("账务查看者")
    try:
        frappe.set_user(viewer)
        assert classification_api.list_leaf_accounts()
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            classification_api.classify_manually(name, "expense", account("560201"), account("1001"), "", True)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            classify_manual(name)
    finally:
        frappe.set_user("Administrator")


def test_document_permissions_apply_to_transaction_and_batch():
    from internal_ledger.tests.test_permissions import user

    name = transaction()
    admin = user("账套管理员")
    frappe.db.set_single_value("Internal Ledger Settings", "company", "")
    try:
        frappe.set_user(admin)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            classify_manual(name)
    finally:
        frappe.set_user("Administrator")


def test_leaf_accounts_are_enabled_current_company_with_only_public_fields():
    from internal_ledger.api.classification_api import list_leaf_accounts

    frappe.db.set_value("Account", account("560202"), "disabled", 1)
    rows = list_leaf_accounts()
    expected = set(frappe.db.get_values("Account", {"company": frappe.flags.classification_company,
        "is_group": 0, "disabled": 0}, "name", pluck=True))
    assert {row["name"] for row in rows} == expected
    assert all(set(row) == {"name", "account_number", "account_name", "root_type"} for row in rows)
    assert account("560202") not in expected


def test_api_accepts_only_six_fields_and_is_post_only():
    from internal_ledger.api.classification_api import classify_manually

    assert list(inspect.signature(classify_manually).parameters) == ["tx_name", "transaction_type",
        "debit_account", "credit_account", "summary", "remember_counterparty"]
    assert frappe.allowed_http_methods_for_whitelisted_func[classify_manually] == ["POST"]
    with unittest.TestCase().assertRaises(TypeError):
        classify_manually(transaction(), "expense", account("560201"), account("1001"), "", True,
                          status="Approved")


def test_transaction_and_batch_reads_are_locked():
    name = transaction()
    tx = frappe.get_doc("Imported Transaction", name)
    original_get_doc = frappe.get_doc
    with patch("frappe.get_doc", wraps=original_get_doc) as get_doc:
        classify_manual(name)
    for doctype, docname in (("Imported Transaction", name), ("Import Batch", tx.import_batch)):
        assert any(call.args[:2] == (doctype, docname) and call.kwargs.get("for_update")
                   for call in get_doc.call_args_list)


def test_draft_failure_rolls_back_rule_and_audit():
    name = transaction()
    before = frappe.db.count("Internal Ledger Audit Event")
    with patch("internal_ledger.accounting.manual_classification.create_draft", side_effect=RuntimeError("draft failed")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "draft failed"):
            classify_manual(name)
    assert not frappe.db.count("Classification Rule", {"company": frappe.flags.classification_company})
    assert frappe.db.count("Internal Ledger Audit Event") == before
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": name})


def test_manual_snapshot_revalidates_without_rules_and_rejects_forgery():
    from internal_ledger.api.review_api import approve_draft, update_draft

    doc = classify_manual(transaction(), remember=False)
    assert doc.is_manual_override and doc.manual_override_by == "Administrator"
    assert not doc.classification_rule
    # Even newly conflicting rules must not rewrite the explicit one-off decision.
    add_rule(debit_account=account("560202"))
    assert approve_draft(doc.name).status == "Approved"
    for field, value in (("transaction_type", "fee"), ("classification_reason", "伪造"),
                         ("classification_rule", frappe.db.get_value("Classification Rule", {}, "name"))):
        doc.reload()
        doc.set(field, value)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            doc.save()
    doc.reload()
    forged = frappe.copy_doc(doc)
    forged.status = "Draft"
    forged.imported_transaction = transaction(counterparty="伪造商户")
    forged.flags.manual_classification = True
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        forged.insert()
    doc.reload()
    frappe.db.set_value("Account", account("560201"), "disabled", 1)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        doc.validate()


def test_manual_snapshot_revalidates_amount_company_and_period():
    from internal_ledger.api.review_api import update_draft

    doc = classify_manual(transaction(), remember=False)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        update_draft(doc.name, [row.as_dict() for row in doc.lines], "无会计年度", "1900-01-01")
    doc.reload()
    for field, value in (("amount", 100), ("status", "Duplicate")):
        tx = frappe.get_doc("Imported Transaction", doc.imported_transaction)
        old = tx.get(field)
        frappe.db.set_value("Imported Transaction", tx.name, field, value)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            doc.validate()
        frappe.db.set_value("Imported Transaction", tx.name, field, old)
    batch_name = frappe.db.get_value("Imported Transaction", doc.imported_transaction, "import_batch")
    frappe.db.set_value("Import Batch", batch_name, "base_currency", "USD")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        doc.validate()

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))